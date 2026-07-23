// SPDX-License-Identifier: GPL-2.0
/*
 * xdpgw-apply — control-plane → data-plane apply helper (M4 #2, double-buffer).
 *
 * The Python worker (DoubleBufferApplier) serializes a full-node config
 * snapshot in the apply_snapshot.h wire format and execs this helper. The
 * helper builds a fresh inactive slot from the snapshot, structurally verifies
 * it, and atomically flips active_config — never touching the live slot until
 * the single commit write (see AD-028).
 *
 * T2 (this file's first cut) ships the two load-bearing primitives the rest of
 * the build path stands on:
 *   - parse_snapshot(): fail-closed decode of the wire format into node_cfg.
 *   - create_inner_like(): replicate an ARRAY_OF_MAPS inner's meta and hand
 *     back a fresh, meta-equal map fd for installation into the inactive slot.
 * The build/verify/flip core (build_inactive_slot/carry_forward_feed/
 * verify_slot/commit) and main()'s pin-open + CLI land in T3–T5. The core
 * functions are static inline so the test harness can #include this file and
 * exercise them in-process under `make test` (skeleton fds).
 */
#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <unistd.h>

#include <bpf/bpf.h>
#include <bpf/libbpf.h>

#include "apply_snapshot.h"
#include "blacklist.h"
#include "fair_budget.h"
#include "fairness.h"
#include "rules.h"
#include "service.h"
#include "whitelist.h"

/*
 * Sanity bounds — a malformed or hostile snapshot must be rejected before any
 * map is touched, so every count is capped and every read is length-checked.
 */
#define APPLY_MAX_BYTES (64u * 1024u * 1024u)
#define APPLY_MAX_SERVICES 65536u
#define APPLY_MAX_LIST_ENTRIES (1u << 20) /* per-service whitelist / sbl cap */
#define APPLY_CONFIG_VERSION 1U
#define APPLY_DEFAULT_NODE_CLEAN_CAPACITY_BPS 5000000000ULL
#define APPLY_DEFAULT_FAIR_K 3ULL
#define APPLY_DEFAULT_FAIR_REF_PKT 512ULL

/* These are deliberately the config-map pins only. Runtime maps, static
 * inners, and tx_devmap remain owned by the loader and are never opened here.
 */
#define APPLY_PIN_DIR "/sys/fs/bpf/xdp_gateway"
#define APPLY_ACTIVE_CONFIG_PIN APPLY_PIN_DIR "/active_config"
#define APPLY_SERVICE_MAP_PIN APPLY_PIN_DIR "/service_map"
#define APPLY_RULE_BLOCK_MAP_PIN APPLY_PIN_DIR "/rule_block_map"
#define APPLY_WHITELIST_BLOOM_PIN APPLY_PIN_DIR "/whitelist_bloom"
#define APPLY_WHITELIST_LPM_PIN APPLY_PIN_DIR "/whitelist_lpm"
#define APPLY_VIP_CONFIG_MAP_PIN APPLY_PIN_DIR "/vip_config_map"
#define APPLY_GLOBAL_BLACKLIST_BLOOM_PIN APPLY_PIN_DIR "/global_blacklist_bloom"
#define APPLY_GLOBAL_BLACKLIST_LPM_PIN APPLY_PIN_DIR "/global_blacklist_lpm"

#define APPLY_UDP_BLOCKED_PORT_BITMAP_PIN APPLY_PIN_DIR "/udp_blocked_port_bitmap"
#define APPLY_FAIR_CONFIG_MAP_PIN APPLY_PIN_DIR "/fair_config_map"
#define APPLY_FAIR_NODE_CONFIG_PIN APPLY_PIN_DIR "/fair_node_config"
#define APPLY_GBL_META_PIN APPLY_PIN_DIR "/gbl_meta"

/* In-memory decode of the wire snapshot. Mirrors apply_snapshot.h field for
 * field; T3 maps these into the BPF map values. Addresses are stored as __be32
 * (network byte order, verbatim wire bytes) to match the data-plane map keys. */
struct cfg_rule {
	uint16_t src_lo;
	uint16_t src_hi;
	uint16_t dst_lo;
	uint16_t dst_hi;
	uint8_t proto;
	uint8_t flags;
};

struct cfg_source {
	uint32_t prefixlen;
	uint32_t addr; /* __be32 */
};

struct cfg_service {
	uint32_t dst_prefixlen;
	uint32_t dst_addr; /* __be32 */
	uint32_t dp_id;
	uint8_t enabled;
	uint8_t wl_flags;
	uint8_t reserved0;
	uint8_t vip_flags;
	uint64_t committed_bps;
	uint64_t ceiling_bps;
	uint64_t vip_pps;
	uint64_t vip_bps;
	uint64_t service_pps;
	uint64_t service_bps;
	uint8_t svc_rl_flags;
	uint16_t rule_count;
	struct cfg_rule rules[RULE_MAX];
	uint32_t wl_count;
	struct cfg_source *wl;
};

struct node_cfg {
	uint32_t schema_version;
	uint32_t snapshot_kind;
	uint32_t service_count;
	struct cfg_service *services;
	uint64_t global_revision;
	uint32_t global_count;
	struct cfg_source *global_entries;
};

/* All config-map fds required by the in-process swap core. The T5 CLI opens
 * these pins; dp-unit builds the same bundle from skeleton fds. */
struct apply_fds {
	int active_config_fd;
	int service_map_fd;
	int rule_block_map_fd;
	int whitelist_bloom_fd;
	int whitelist_lpm_fd;
	int vip_config_map_fd;
	int global_blacklist_bloom_fd;
	int global_blacklist_lpm_fd;

	int udp_blocked_port_bitmap_fd;
	int fair_config_map_fd;
	int fair_node_config_fd;
	int gbl_meta_fd;
	uint32_t active_slot;
	uint32_t inactive_slot;
	uint32_t version;
	const struct node_cfg *node;
	uint64_t node_capacity_bps;
	uint64_t sum_committed_bps;
	uint32_t global_bloom_fill;
	uint8_t global_meta_flags;
};

/* These seams exist only in the in-process dp-unit translation unit. They are
 * deliberately absent from the helper binary: no runtime flag can turn a
 * production apply into a forced failure. */
#ifdef XDPGW_APPLY_TEST
enum apply_test_fault {
	APPLY_TEST_FAULT_NONE,
	APPLY_TEST_FAULT_BUILD_INSTALL,
	APPLY_TEST_FAULT_VERIFY_MISMATCH,
};

static enum apply_test_fault apply_test_fault;
static uint32_t apply_test_open_fresh_fds;

static inline void apply_test_set_fault(enum apply_test_fault fault)
{
	apply_test_fault = fault;
}

static inline uint32_t apply_test_fresh_fd_count(void)
{
	return apply_test_open_fresh_fds;
}
#endif

enum apply_service_outer {
	APPLY_SERVICE_MAP,
	APPLY_RULE_BLOCK_MAP,
	APPLY_WHITELIST_BLOOM,
	APPLY_WHITELIST_LPM,
	APPLY_VIP_CONFIG_MAP,
	APPLY_FAIR_CONFIG_MAP,
	APPLY_SERVICE_OUTER_COUNT,
};

/* Bounds-checked little-endian byte reader over a fixed buffer (fail-closed). */
struct rdcur {
	const uint8_t *p;
	const uint8_t *end;
};

static inline int rd_bytes(struct rdcur *c, void *dst, size_t n)
{
	if ((size_t)(c->end - c->p) < n)
		return -1;
	memcpy(dst, c->p, n);
	c->p += n;
	return 0;
}

static inline int rd_u8(struct rdcur *c, uint8_t *v)
{
	if ((size_t)(c->end - c->p) < 1)
		return -1;
	*v = c->p[0];
	c->p += 1;
	return 0;
}

static inline int rd_u16le(struct rdcur *c, uint16_t *v)
{
	if ((size_t)(c->end - c->p) < 2)
		return -1;
	*v = (uint16_t)c->p[0] | ((uint16_t)c->p[1] << 8);
	c->p += 2;
	return 0;
}

static inline int rd_u32le(struct rdcur *c, uint32_t *v)
{
	if ((size_t)(c->end - c->p) < 4)
		return -1;
	*v = (uint32_t)c->p[0] | ((uint32_t)c->p[1] << 8) |
	     ((uint32_t)c->p[2] << 16) | ((uint32_t)c->p[3] << 24);
	c->p += 4;
	return 0;
}

static inline int rd_u64le(struct rdcur *c, uint64_t *v)
{
	if ((size_t)(c->end - c->p) < 8)
		return -1;
	*v = 0;
	for (int i = 0; i < 8; i++)
		*v |= (uint64_t)c->p[i] << (8 * i);
	c->p += 8;
	return 0;
}

/* __be32 field: the 4 wire bytes are already network order; copy them verbatim
 * so the stored value equals inet_addr()/htonl() of the same address. */
static inline int rd_be32(struct rdcur *c, uint32_t *v)
{
	return rd_bytes(c, v, 4);
}

static inline void free_node_cfg(struct node_cfg *node)
{
	if (!node)
		return;
	if (node->services) {
		for (uint32_t i = 0; i < node->service_count; i++) {
			free(node->services[i].wl);
		}
		free(node->services);
	}
	free(node->global_entries);
	memset(node, 0, sizeof(*node));
}

static inline int parse_source_list(struct rdcur *c, uint32_t *count_out,
				    struct cfg_source **list_out)
{
	uint32_t count = 0;
	struct cfg_source *list = NULL;

	*count_out = 0;
	*list_out = NULL;

	if (rd_u32le(c, &count) != 0)
		return -1;
	if (count > APPLY_MAX_LIST_ENTRIES)
		return -1;
	if (count == 0)
		return 0;

	list = calloc(count, sizeof(*list));
	if (!list)
		return -1;

	for (uint32_t i = 0; i < count; i++) {
		if (rd_u32le(c, &list[i].prefixlen) != 0 ||
		    rd_be32(c, &list[i].addr) != 0) {
			free(list);
			return -1;
		}
	}

	*count_out = count;
	*list_out = list;
	return 0;
}

static inline int parse_service(struct rdcur *c, struct cfg_service *svc)
{
	uint16_t rule_count = 0;

	memset(svc, 0, sizeof(*svc));

	if (rd_u32le(c, &svc->dst_prefixlen) != 0 ||
	    rd_be32(c, &svc->dst_addr) != 0 ||
	    rd_u32le(c, &svc->dp_id) != 0 ||
	    rd_u8(c, &svc->enabled) != 0 ||
	    rd_u8(c, &svc->wl_flags) != 0 ||
	    rd_u8(c, &svc->reserved0) != 0 ||
	    rd_u64le(c, &svc->committed_bps) != 0 ||
	    rd_u64le(c, &svc->ceiling_bps) != 0 ||
	    rd_u64le(c, &svc->vip_pps) != 0 ||
	    rd_u64le(c, &svc->vip_bps) != 0 ||
	    rd_u8(c, &svc->vip_flags) != 0 ||
	    rd_u64le(c, &svc->service_pps) != 0 ||
	    rd_u64le(c, &svc->service_bps) != 0 ||
	    rd_u8(c, &svc->svc_rl_flags) != 0 ||
	    rd_u16le(c, &rule_count) != 0)
		return -1;

	if (svc->reserved0 != 0)
		return -1;

	if (rule_count > RULE_MAX)
		return -1;
	svc->rule_count = rule_count;

	for (uint16_t i = 0; i < rule_count; i++) {
		struct cfg_rule *r = &svc->rules[i];

		if (rd_u16le(c, &r->src_lo) != 0 ||
		    rd_u16le(c, &r->src_hi) != 0 ||
		    rd_u16le(c, &r->dst_lo) != 0 ||
		    rd_u16le(c, &r->dst_hi) != 0 ||
		    rd_u8(c, &r->proto) != 0 || rd_u8(c, &r->flags) != 0)
			return -1;
	}

	if (parse_source_list(c, &svc->wl_count, &svc->wl) != 0)
		return -1;

	return 0;
}

static inline int global_entry_valid(const struct cfg_source *entry)
{
	uint32_t addr;
	uint32_t mask;

	if (entry->prefixlen > 32)
		return 0;
	addr = ntohl(entry->addr);
	mask = entry->prefixlen ? UINT32_MAX << (32 - entry->prefixlen) : 0;
	return (addr & ~mask) == 0;
}

static inline int global_entries_sorted(const struct cfg_source *previous,
					const struct cfg_source *current)
{
	if (previous->prefixlen != current->prefixlen)
		return previous->prefixlen < current->prefixlen;
	return memcmp(&previous->addr, &current->addr, sizeof(current->addr)) < 0;
}

static inline int parse_global_deny(struct rdcur *c, struct node_cfg *node)
{
	struct cfg_source *entries = NULL;
	uint64_t revision = 0;
	uint32_t count = 0;

	if (rd_u64le(c, &revision) != 0 || rd_u32le(c, &count) != 0 ||
	    count > APPLY_SNAPSHOT_GLOBAL_DENY_MAX_ENTRIES ||
	    count > (size_t)(c->end - c->p) / APPLY_SNAPSHOT_GLOBAL_ENTRY_SIZE)
		return -1;
	if (count > 0) {
		entries = calloc(count, sizeof(*entries));
		if (!entries)
			return -1;
	}

	for (uint32_t i = 0; i < count; i++) {
		if (rd_u32le(c, &entries[i].prefixlen) != 0 ||
		    rd_be32(c, &entries[i].addr) != 0 ||
		    !global_entry_valid(&entries[i]) ||
		    (i > 0 && !global_entries_sorted(&entries[i - 1], &entries[i]))) {
			free(entries);
			return -1;
		}
	}

	node->global_revision = revision;
	node->global_count = count;
	node->global_entries = entries;
	return 0;
}

/*
 * parse_snapshot — decode the wire snapshot at `path` into `out`.
 * Fail-closed: rejects a bad magic, schema, kind, count, truncation, or global
 * CIDR ordering/canonicality violation before returning success. On failure
 * `out` is left empty and any partial allocation is released; on success the
 * caller owns the result and must free_node_cfg() it.
 */
static inline int parse_snapshot(const char *path, struct node_cfg *out)
{
	uint8_t magic[APPLY_SNAPSHOT_MAGIC_SIZE];
	uint8_t *buf = NULL;
	struct rdcur c;
	long size;
	size_t got;
	uint32_t schema = 0;
	uint32_t kind = 0;
	uint32_t count = 0;
	FILE *f;

	memset(out, 0, sizeof(*out));

	f = fopen(path, "rb");
	if (!f) {
		fprintf(stderr, "xdpgw-apply: open %s: %s\n", path,
			strerror(errno));
		return -1;
	}
	if (fseek(f, 0, SEEK_END) != 0 || (size = ftell(f)) < 0 ||
	    fseek(f, 0, SEEK_SET) != 0) {
		fprintf(stderr, "xdpgw-apply: seek %s: %s\n", path,
			strerror(errno));
		fclose(f);
		return -1;
	}
	if ((uint64_t)size < APPLY_SNAPSHOT_HEADER_SIZE ||
	    (uint64_t)size > APPLY_MAX_BYTES) {
		fprintf(stderr, "xdpgw-apply: snapshot %s size %ld out of range\n",
			path, size);
		fclose(f);
		return -1;
	}

	buf = malloc((size_t)size);
	if (!buf) {
		fclose(f);
		return -1;
	}
	got = fread(buf, 1, (size_t)size, f);
	fclose(f);
	if (got != (size_t)size) {
		free(buf);
		return -1;
	}

	c.p = buf;
	c.end = buf + size;

	if (rd_bytes(&c, magic, sizeof(magic)) != 0 ||
	    memcmp(magic, APPLY_SNAPSHOT_MAGIC, APPLY_SNAPSHOT_MAGIC_SIZE) != 0) {
		fprintf(stderr, "xdpgw-apply: %s bad magic\n", path);
		free(buf);
		return -1;
	}
	if (rd_u32le(&c, &schema) != 0 ||
	    schema != APPLY_SNAPSHOT_SCHEMA_VERSION) {
		fprintf(stderr,
			"xdpgw-apply: %s schema_version %u unsupported (want %u)\n",
			path, schema, APPLY_SNAPSHOT_SCHEMA_VERSION);
		free(buf);
		return -1;
	}
	out->schema_version = schema;
	if (rd_u32le(&c, &kind) != 0) {
		fprintf(stderr, "xdpgw-apply: %s missing snapshot kind\n", path);
		goto fail;
	}
	out->snapshot_kind = kind;
	switch (kind) {
	case APPLY_SNAPSHOT_KIND_SERVICE_FULL:
		if (rd_u32le(&c, &count) != 0 || count > APPLY_MAX_SERVICES) {
			fprintf(stderr,
				"xdpgw-apply: %s service_count %u out of range\n",
				path, count);
			goto fail;
		}
		if (count > 0) {
			out->services = calloc(count, sizeof(*out->services));
			if (!out->services)
				goto fail;
		}
		for (uint32_t i = 0; i < count; i++) {
			out->service_count = i + 1; /* release partial service on failure */
			if (parse_service(&c, &out->services[i]) != 0) {
				fprintf(stderr,
					"xdpgw-apply: %s truncated/invalid at service %u\n",
					path, i);
				goto fail;
			}
		}
		break;
	case APPLY_SNAPSHOT_KIND_GLOBAL_DENY:
		if (parse_global_deny(&c, out) != 0) {
			fprintf(stderr,
				"xdpgw-apply: %s truncated/invalid global deny snapshot\n",
				path);
			goto fail;
		}
		break;
	default:
		fprintf(stderr, "xdpgw-apply: %s snapshot kind %u unsupported\n",
			path, kind);
		goto fail;
	}
	if (c.p != c.end) {
		fprintf(stderr, "xdpgw-apply: %s has trailing snapshot bytes\n", path);
		goto fail;
	}

	free(buf);
	return 0;

fail:
	free_node_cfg(out);
	free(buf);
	return -1;
}

/*
 * create_inner_like — the load-bearing novel composition (DBS-09).
 * Reads the meta of the inner map installed at `src_slot` of an ARRAY_OF_MAPS
 * outer and creates a fresh, meta-equal map (type/key/value/max_entries/flags/
 * map_extra) with btf_fd=0 (map_meta_equal ignores BTF), ready to be populated
 * and installed into the inactive slot. Returns a new fd (caller closes) or -1.
 */
static inline int create_inner_like(int outer_fd, uint32_t src_slot)
{
	struct bpf_map_info info;
	uint32_t inner_id = 0;
	uint32_t len = sizeof(info);
	int src_fd;
	int fresh;

	if (bpf_map_lookup_elem(outer_fd, &src_slot, &inner_id) != 0)
		return -1;

	src_fd = bpf_map_get_fd_by_id(inner_id);
	if (src_fd < 0)
		return -1;

	memset(&info, 0, sizeof(info));
	if (bpf_map_get_info_by_fd(src_fd, &info, &len) != 0) {
		close(src_fd);
		return -1;
	}
	close(src_fd);

	LIBBPF_OPTS(bpf_map_create_opts, opts, .map_flags = info.map_flags,
		    .map_extra = info.map_extra);

	fresh = bpf_map_create(info.type, NULL, info.key_size, info.value_size,
			       info.max_entries, &opts);
	if (fresh < 0)
		fprintf(stderr, "xdpgw-apply: create_inner_like slot %u: %s\n",
			src_slot, strerror(errno));
	return fresh;
}

static inline int apply_create_fresh_inner(int outer_fd, uint32_t src_slot)
{
	int fresh = create_inner_like(outer_fd, src_slot);

#ifdef XDPGW_APPLY_TEST
	if (fresh >= 0)
		apply_test_open_fresh_fds++;
#endif
	return fresh;
}

static inline void apply_close_fresh_inner(int fd)
{
	if (fd < 0)
		return;
	close(fd);
#ifdef XDPGW_APPLY_TEST
	apply_test_open_fresh_fds--;
#endif
}

static inline int apply_inner_fd(int outer_fd, uint32_t slot)
{
	uint32_t inner_id = 0;

	if (bpf_map_lookup_elem(outer_fd, &slot, &inner_id) != 0)
		return -1;
	return bpf_map_get_fd_by_id(inner_id);
}

static inline int apply_read_active(struct apply_fds *fds)
{
	struct active_config active;
	uint32_t zero = 0;

	if (bpf_map_lookup_elem(fds->active_config_fd, &zero, &active) != 0) {
		fprintf(stderr, "xdpgw-apply: read active_config: %s\n",
			strerror(errno));
		return -1;
	}
	if (active.active_slot >= SERVICE_SLOTS) {
		fprintf(stderr, "xdpgw-apply: invalid active slot %u\n",
			active.active_slot);
		return -1;
	}

	fds->active_slot = active.active_slot;
	fds->inactive_slot = 1 - active.active_slot;
	fds->version = active.version;
	return 0;
}

static inline int apply_parse_u64_env(const char *name, uint64_t *value)
{
	const char *text = getenv(name);
	char *end;
	unsigned long long parsed;

	if (!text || text[0] == '\0')
		return 0;

	errno = 0;
	parsed = strtoull(text, &end, 10);
	if (errno || end == text || *end != '\0') {
		fprintf(stderr, "xdpgw-apply: invalid %s=%s\n", name, text);
		return -1;
	}
	*value = parsed;
	return 0;
}

static inline int apply_node_knobs(uint64_t *capacity, uint64_t *k,
				   uint64_t *ref_pkt)
{
	*capacity = APPLY_DEFAULT_NODE_CLEAN_CAPACITY_BPS;
	*k = APPLY_DEFAULT_FAIR_K;
	*ref_pkt = APPLY_DEFAULT_FAIR_REF_PKT;

	if (apply_parse_u64_env("XDPGW_NODE_CLEAN_CAPACITY_BPS", capacity) != 0 ||
	    apply_parse_u64_env("XDPGW_FAIR_K", k) != 0 ||
	    apply_parse_u64_env("XDPGW_FAIR_REF_PKT", ref_pkt) != 0)
		return -1;
	if (*k == 0 || *ref_pkt == 0) {
		fprintf(stderr, "xdpgw-apply: fair K and reference packet must be non-zero\n");
		return -1;
	}
	*capacity = clamp_fair_rate(*capacity);
	return 0;
}

static inline int apply_install_inner(int outer_fd, uint32_t slot, int inner_fd)
{
#ifdef XDPGW_APPLY_TEST
	if (apply_test_fault == APPLY_TEST_FAULT_BUILD_INSTALL) {
		errno = EIO;
		return -1;
	}
#endif
	if (bpf_map_update_elem(outer_fd, &slot, &inner_fd, BPF_ANY) != 0) {
		fprintf(stderr, "xdpgw-apply: install inactive inner: %s\n",
			strerror(errno));
		return -1;
	}
	return 0;
}

static inline int apply_write_service(int service_fd, int rule_fd, int wl_bloom_fd,
					     int wl_lpm_fd, int vip_fd,
					     int fair_fd,
					     const struct cfg_service *service,
					     uint64_t k, uint64_t ref_pkt,
					     uint64_t *sum_committed)
{
	struct service_key service_key = {
		.prefixlen = service->dst_prefixlen,
		.addr = service->dst_addr,
	};
	struct service_val service_val = {
		.service_id = service->dp_id,
		.enabled = service->enabled,
		.wl_flags = service->wl_flags,
		.reserved0 = 0,
	};
	struct rule_block block = {
		.version = APPLY_CONFIG_VERSION,
		.rule_count = service->rule_count,
	};
	struct fair_budget budget;
	struct fair_config fair_config = {};
	uint32_t service_id = service->dp_id;
	uint16_t i;

	if (service->dst_prefixlen > 32 || service->dp_id == 0 ||
	    service->enabled > 1 || service->reserved0 != 0 || service->rule_count > RULE_MAX ||
	    service->committed_bps > service->ceiling_bps) {
		fprintf(stderr, "xdpgw-apply: invalid service %u configuration\n",
			service->dp_id);
		return -1;
	}

	for (i = 0; i < service->rule_count; i++) {
		block.rules[i].src_lo = service->rules[i].src_lo;
		block.rules[i].src_hi = service->rules[i].src_hi;
		block.rules[i].dst_lo = service->rules[i].dst_lo;
		block.rules[i].dst_hi = service->rules[i].dst_hi;
		block.rules[i].proto = service->rules[i].proto;
		block.rules[i].flags = service->rules[i].flags;
	}
	if (bpf_map_update_elem(service_fd, &service_key, &service_val,
				BPF_ANY) != 0 ||
	    bpf_map_update_elem(rule_fd, &service_id, &block, BPF_ANY) != 0) {
		fprintf(stderr, "xdpgw-apply: write service %u: %s\n",
			service_id, strerror(errno));
		return -1;
	}

	for (i = 0; i < service->wl_count; i++) {
		const struct cfg_source *source = &service->wl[i];
		struct wl_lpm_key lpm_key = {
			.prefixlen = 32 + source->prefixlen,
			.service_id = htonl(service_id),
			.src = source->addr,
		};
		__u8 present = 1;

		if (source->prefixlen > 32) {
			fprintf(stderr, "xdpgw-apply: invalid whitelist prefix\n");
			return -1;
		}
		if (source->prefixlen >= WL_BLOOM_PREFIX) {
			struct wl_bloom_key bloom_key = {
				.service_id = htonl(service_id),
				.src24 = htonl(ntohl(source->addr) & WL_SRC24_MASK),
			};

			if (bpf_map_update_elem(wl_bloom_fd, NULL, &bloom_key,
						BPF_ANY) != 0)
				return -1;
		}
		if (bpf_map_update_elem(wl_lpm_fd, &lpm_key, &present, BPF_ANY) != 0)
			return -1;
	}

	if (service->wl_flags & WL_F_ACTIVE) {
		struct vip_config vip_config = {
			.version = APPLY_CONFIG_VERSION,
			.flags = service->vip_flags,
			.pps = service->vip_pps,
			.bps = service->vip_bps,
		};

		if (bpf_map_update_elem(vip_fd, &service_id, &vip_config,
					BPF_ANY) != 0)
			return -1;
	}

	budget = fair_budget(service->committed_bps, service->ceiling_bps, k,
				     ref_pkt);
	fair_config.version = APPLY_CONFIG_VERSION;
	fair_config.committed_bps = budget.committed_bps;
	fair_config.burst_bps = budget.burst_bps;
	fair_config.cap_bps = budget.cap_bps;
	fair_config.cap_pps = budget.cap_pps;
	if (bpf_map_update_elem(fair_fd, &service_id, &fair_config, BPF_ANY) != 0)
		return -1;

	if (UINT64_MAX - *sum_committed < budget.committed_bps)
		*sum_committed = UINT64_MAX;
	else
		*sum_committed += budget.committed_bps;
	return 0;
}

/* Build fresh service-scoped inners from one snapshot. Only the inactive slot
 * is changed; active_config is read once here and remains untouched. */
static inline int build_inactive_slot(struct apply_fds *fds,
					      const struct node_cfg *node)
{
	int outers[APPLY_SERVICE_OUTER_COUNT];
	int fresh[APPLY_SERVICE_OUTER_COUNT];
	struct fair_node_config fair_node_config;
	uint64_t capacity;
	uint64_t k;
	uint64_t ref_pkt;
	uint64_t sum_committed = 0;
	uint32_t i;
	int err = -1;

	if (!fds || !node ||
	    node->schema_version != APPLY_SNAPSHOT_SCHEMA_VERSION ||
	    (node->service_count && !node->services)) {
		errno = EINVAL;
		return -1;
	}
	if (apply_read_active(fds) != 0 ||
	    apply_node_knobs(&capacity, &k, &ref_pkt) != 0)
		return -1;

	outers[APPLY_SERVICE_MAP] = fds->service_map_fd;
	outers[APPLY_RULE_BLOCK_MAP] = fds->rule_block_map_fd;
	outers[APPLY_WHITELIST_BLOOM] = fds->whitelist_bloom_fd;
	outers[APPLY_WHITELIST_LPM] = fds->whitelist_lpm_fd;
	outers[APPLY_VIP_CONFIG_MAP] = fds->vip_config_map_fd;
	outers[APPLY_FAIR_CONFIG_MAP] = fds->fair_config_map_fd;
	for (i = 0; i < APPLY_SERVICE_OUTER_COUNT; i++)
		fresh[i] = -1;

	for (i = 0; i < APPLY_SERVICE_OUTER_COUNT; i++) {
		fresh[i] = apply_create_fresh_inner(outers[i], fds->active_slot);
		if (fresh[i] < 0)
			goto out;
	}

	for (i = 0; i < node->service_count; i++) {
		if (apply_write_service(fresh[APPLY_SERVICE_MAP],
					fresh[APPLY_RULE_BLOCK_MAP],
					fresh[APPLY_WHITELIST_BLOOM],
					fresh[APPLY_WHITELIST_LPM],
					fresh[APPLY_VIP_CONFIG_MAP],
					fresh[APPLY_FAIR_CONFIG_MAP],
					&node->services[i], k, ref_pkt,
					&sum_committed) != 0)
			goto out;
	}

	fair_node_config.version = APPLY_CONFIG_VERSION;
	fair_node_config._pad = 0;
	fair_node_config.headroom_bps = node_headroom(capacity, sum_committed);
	if (bpf_map_update_elem(fds->fair_node_config_fd, &fds->inactive_slot,
				&fair_node_config, BPF_ANY) != 0) {
		fprintf(stderr, "xdpgw-apply: write fair_node_config: %s\n",
			strerror(errno));
		goto out;
	}

	for (i = 0; i < APPLY_SERVICE_OUTER_COUNT; i++) {
		if (apply_install_inner(outers[i], fds->inactive_slot, fresh[i]) != 0)
			goto out;
	}

	fds->node = node;
	fds->node_capacity_bps = capacity;
	fds->sum_committed_bps = sum_committed;
	err = 0;
out:
	for (i = 0; i < APPLY_SERVICE_OUTER_COUNT; i++) {
		apply_close_fresh_inner(fresh[i]);
	}
	return err;
}

static inline int apply_copy_outer_inner(int outer_fd, uint32_t active_slot,
						 uint32_t inactive_slot)
{
	int inner_fd = apply_inner_fd(outer_fd, active_slot);
	int err;

	if (inner_fd < 0)
		return -1;
	err = apply_install_inner(outer_fd, inactive_slot, inner_fd);
	close(inner_fd);
	return err;
}

static inline int apply_outer_id(int outer_fd, uint32_t slot, uint32_t *id)
{
	return bpf_map_lookup_elem(outer_fd, &slot, id);
}

static inline void apply_service_outers(const struct apply_fds *fds,
					int outers[APPLY_SERVICE_OUTER_COUNT])
{
	outers[APPLY_SERVICE_MAP] = fds->service_map_fd;
	outers[APPLY_RULE_BLOCK_MAP] = fds->rule_block_map_fd;
	outers[APPLY_WHITELIST_BLOOM] = fds->whitelist_bloom_fd;
	outers[APPLY_WHITELIST_LPM] = fds->whitelist_lpm_fd;
	outers[APPLY_VIP_CONFIG_MAP] = fds->vip_config_map_fd;
	outers[APPLY_FAIR_CONFIG_MAP] = fds->fair_config_map_fd;
}

/* Feed-owned maps are immutable to a service apply: reinstall active inners by
 * fd and copy gbl_meta, avoiding any blacklist rebuild. */
static inline int carry_forward_feed(struct apply_fds *fds)
{
	struct gbl_meta meta;

	if (!fds)
		return -1;
	if (apply_copy_outer_inner(fds->global_blacklist_bloom_fd,
				   fds->active_slot, fds->inactive_slot) != 0 ||
	    apply_copy_outer_inner(fds->global_blacklist_lpm_fd,
				   fds->active_slot, fds->inactive_slot) != 0 ||
	    apply_copy_outer_inner(fds->udp_blocked_port_bitmap_fd,
				   fds->active_slot, fds->inactive_slot) != 0)
		return -1;
	if (bpf_map_lookup_elem(fds->gbl_meta_fd, &fds->active_slot, &meta) != 0 ||
	    bpf_map_update_elem(fds->gbl_meta_fd, &fds->inactive_slot, &meta,
				BPF_ANY) != 0)
		return -1;
	return 0;
}

/* A GLOBAL_DENY snapshot is the inverse of a service rebuild: service-owned
 * maps retain their active identities while the global maps are rebuilt. */
static inline int carry_forward_service_config(struct apply_fds *fds)
{
	int outers[APPLY_SERVICE_OUTER_COUNT];
	struct fair_node_config node_config;
	uint32_t i;

	if (!fds)
		return -1;
	apply_service_outers(fds, outers);
	for (i = 0; i < APPLY_SERVICE_OUTER_COUNT; i++) {
		if (apply_copy_outer_inner(outers[i], fds->active_slot,
					  fds->inactive_slot) != 0)
			return -1;
	}
	if (apply_copy_outer_inner(fds->udp_blocked_port_bitmap_fd,
				  fds->active_slot, fds->inactive_slot) != 0 ||
	    bpf_map_lookup_elem(fds->fair_node_config_fd, &fds->active_slot,
					&node_config) != 0 ||
	    bpf_map_update_elem(fds->fair_node_config_fd, &fds->inactive_slot,
					&node_config, BPF_ANY) != 0)
		return -1;
	return 0;
}

/* Return the expected bloom construction policy. Prefixes 16..23 expand to
 * their /24 coverage; anything broader or a 2M-entry overflow activates the
 * LPM-only escape path, so the fresh bloom intentionally remains empty. */
static inline int global_bloom_plan(const struct node_cfg *node,
					uint8_t *flags_out, uint32_t *fill_out)
{
	uint64_t fill = 0;
	uint8_t flags = 0;
	uint32_t i;

	if (!node || node->global_count > APPLY_SNAPSHOT_GLOBAL_DENY_MAX_ENTRIES ||
	    node->global_count > GBL_LPM_MAX_ENTRIES ||
	    (node->global_count && !node->global_entries))
		return -1;
	if (node->global_count)
		flags |= GBL_F_ACTIVE;
	for (i = 0; i < node->global_count; i++) {
		const struct cfg_source *entry = &node->global_entries[i];
		uint32_t buckets;

		if (!global_entry_valid(entry) ||
		    (i && !global_entries_sorted(&node->global_entries[i - 1],
						       entry)))
			return -1;
		if (entry->prefixlen < GBL_EXPAND_FLOOR) {
			flags |= GBL_F_HAS_BROAD;
			continue;
		}
		buckets = entry->prefixlen < GBL_BLOOM_PREFIX
				  ? 1U << (GBL_BLOOM_PREFIX - entry->prefixlen)
				  : 1U;
		if (fill > GBL_BLOOM_MAX_ENTRIES - buckets)
			flags |= GBL_F_HAS_BROAD;
		else
			fill += buckets;
	}
	*flags_out = flags;
	*fill_out = flags & GBL_F_HAS_BROAD ? 0 : (uint32_t)fill;
	return 0;
}

static inline int global_bloom_insert(int bloom_fd,
				      const struct cfg_source *entry)
{
	uint32_t first;
	uint32_t buckets;
	uint32_t i;

	if (entry->prefixlen < GBL_EXPAND_FLOOR)
		return 0;
	first = ntohl(entry->addr) & BL_SRC24_MASK;
	buckets = entry->prefixlen < GBL_BLOOM_PREFIX
			  ? 1U << (GBL_BLOOM_PREFIX - entry->prefixlen)
			  : 1U;
	for (i = 0; i < buckets; i++) {
		__be32 key = htonl(first + i * 256U);

		if (bpf_map_update_elem(bloom_fd, NULL, &key, BPF_ANY) != 0)
			return -1;
	}
	return 0;
}

static inline int build_global_deny_slot(struct apply_fds *fds,
					 const struct node_cfg *node)
{
	struct gbl_meta meta = {};
	uint8_t flags;
	uint32_t bloom_fill;
	uint32_t i;
	int bloom_fd = -1;
	int lpm_fd = -1;
	int err = -1;

	if (!fds || !node ||
	    node->schema_version != APPLY_SNAPSHOT_SCHEMA_VERSION ||
	    node->snapshot_kind != APPLY_SNAPSHOT_KIND_GLOBAL_DENY ||
	    global_bloom_plan(node, &flags, &bloom_fill) != 0) {
		errno = EINVAL;
		return -1;
	}
	bloom_fd = apply_create_fresh_inner(fds->global_blacklist_bloom_fd,
					 fds->active_slot);
	lpm_fd = apply_create_fresh_inner(fds->global_blacklist_lpm_fd,
				       fds->active_slot);
	if (bloom_fd < 0 || lpm_fd < 0)
		goto out;
	for (i = 0; i < node->global_count; i++) {
		const struct cfg_source *entry = &node->global_entries[i];
		struct bl_lpm_key key = {
			.prefixlen = entry->prefixlen,
			.src = entry->addr,
		};
		__u8 present = 1;

		if (bpf_map_update_elem(lpm_fd, &key, &present, BPF_ANY) != 0 ||
		    (!(flags & GBL_F_HAS_BROAD) &&
		     global_bloom_insert(bloom_fd, entry) != 0))
			goto out;
	}
	meta.flags = flags;
	if (bpf_map_update_elem(fds->gbl_meta_fd, &fds->inactive_slot, &meta,
				BPF_ANY) != 0 ||
	    apply_install_inner(fds->global_blacklist_bloom_fd,
				fds->inactive_slot, bloom_fd) != 0 ||
	    apply_install_inner(fds->global_blacklist_lpm_fd,
				fds->inactive_slot, lpm_fd) != 0)
		goto out;
	fds->node = node;
	fds->global_bloom_fill = bloom_fill;
	fds->global_meta_flags = flags;
	err = 0;
out:
	apply_close_fresh_inner(bloom_fd);
	apply_close_fresh_inner(lpm_fd);
	return err;
}

/* Structural read-back of the inactive candidate. This must remain side-effect
 * free so a mismatch aborts before commit. */
static inline int verify_slot(struct apply_fds *fds)
{
	int outers[APPLY_SERVICE_OUTER_COUNT];
	int inners[APPLY_SERVICE_OUTER_COUNT];
	struct fair_node_config node_config;
	struct gbl_meta active_meta;
	struct gbl_meta inactive_meta;
	uint32_t i;
	int err = -1;

	if (!fds || !fds->node)
		return -1;
	outers[APPLY_SERVICE_MAP] = fds->service_map_fd;
	outers[APPLY_RULE_BLOCK_MAP] = fds->rule_block_map_fd;
	outers[APPLY_WHITELIST_BLOOM] = fds->whitelist_bloom_fd;
	outers[APPLY_WHITELIST_LPM] = fds->whitelist_lpm_fd;
	outers[APPLY_VIP_CONFIG_MAP] = fds->vip_config_map_fd;
	outers[APPLY_FAIR_CONFIG_MAP] = fds->fair_config_map_fd;
	for (i = 0; i < APPLY_SERVICE_OUTER_COUNT; i++)
		inners[i] = -1;
	for (i = 0; i < APPLY_SERVICE_OUTER_COUNT; i++) {
		inners[i] = apply_inner_fd(outers[i], fds->inactive_slot);
		if (inners[i] < 0)
			goto out;
	}

	for (i = 0; i < fds->node->service_count; i++) {
		const struct cfg_service *service = &fds->node->services[i];
		struct service_key key = {
			.prefixlen = service->dst_prefixlen,
			.addr = service->dst_addr,
		};
		struct service_val value;
		struct rule_block block;

		if (!service->enabled)
			continue;
		if (bpf_map_lookup_elem(inners[APPLY_SERVICE_MAP], &key, &value) != 0 ||
		    value.service_id != service->dp_id || !value.enabled ||
		    value.wl_flags != service->wl_flags ||
		    value.reserved0 != 0 ||
		    bpf_map_lookup_elem(inners[APPLY_RULE_BLOCK_MAP], &service->dp_id,
					&block) != 0 ||
		    block.version != APPLY_CONFIG_VERSION ||
		    block.rule_count != service->rule_count)
			goto out;
	}

	if (bpf_map_lookup_elem(fds->fair_node_config_fd, &fds->inactive_slot,
				&node_config) != 0 ||
	    node_config.version != APPLY_CONFIG_VERSION ||
	    node_config.headroom_bps != node_headroom(fds->node_capacity_bps,
						       fds->sum_committed_bps) ||
	    bpf_map_lookup_elem(fds->gbl_meta_fd, &fds->active_slot,
				&active_meta) != 0 ||
	    bpf_map_lookup_elem(fds->gbl_meta_fd, &fds->inactive_slot,
				&inactive_meta) != 0 ||
	    active_meta.flags != inactive_meta.flags)
		goto out;

#ifdef XDPGW_APPLY_TEST
	if (apply_test_fault == APPLY_TEST_FAULT_VERIFY_MISMATCH)
		goto out;
#endif

	err = 0;
out:
	for (i = 0; i < APPLY_SERVICE_OUTER_COUNT; i++) {
		if (inners[i] >= 0)
			close(inners[i]);
	}
	return err;
}

static inline int verify_global_lpm(int lpm_fd, const struct node_cfg *node)
{
	struct bl_lpm_key key;
	struct bl_lpm_key next;
	struct bl_lpm_key *previous = NULL;
	uint32_t count = 0;
	uint32_t i;

	while (bpf_map_get_next_key(lpm_fd, previous, &next) == 0) {
		key = next;
		previous = &key;
		if (++count > node->global_count)
			return -1;
	}
	if (errno != ENOENT || count != node->global_count)
		return -1;
	for (i = 0; i < node->global_count; i++) {
		const struct cfg_source *entry = &node->global_entries[i];
		struct bl_lpm_key expected = {
			.prefixlen = entry->prefixlen,
			.src = entry->addr,
		};
		__u8 present;

		if (bpf_map_lookup_elem(lpm_fd, &expected, &present) != 0 ||
		    present != 1)
			return -1;
	}
	return 0;
}

static inline int verify_global_deny_slot(struct apply_fds *fds)
{
	int service_outers[APPLY_SERVICE_OUTER_COUNT];
	struct fair_node_config active_node;
	struct fair_node_config inactive_node;
	struct gbl_meta inactive_meta;
	uint32_t active_id;
	uint32_t inactive_id;
	uint8_t expected_flags;
	uint32_t expected_fill;
	uint32_t i;
	int lpm_fd = -1;
	int err = -1;

	if (!fds || !fds->node ||
	    fds->node->snapshot_kind != APPLY_SNAPSHOT_KIND_GLOBAL_DENY ||
	    global_bloom_plan(fds->node, &expected_flags, &expected_fill) != 0 ||
	    expected_flags != fds->global_meta_flags ||
	    expected_fill != fds->global_bloom_fill)
		return -1;
	apply_service_outers(fds, service_outers);
	for (i = 0; i < APPLY_SERVICE_OUTER_COUNT; i++) {
		if (apply_outer_id(service_outers[i], fds->active_slot,
				   &active_id) != 0 ||
		    apply_outer_id(service_outers[i], fds->inactive_slot,
				   &inactive_id) != 0 ||
		    active_id != inactive_id)
			goto out;
	}
	if (apply_outer_id(fds->udp_blocked_port_bitmap_fd, fds->active_slot,
			   &active_id) != 0 ||
	    apply_outer_id(fds->udp_blocked_port_bitmap_fd, fds->inactive_slot,
			   &inactive_id) != 0 ||
	    active_id != inactive_id ||
	    apply_outer_id(fds->global_blacklist_bloom_fd, fds->active_slot,
			   &active_id) != 0 ||
	    apply_outer_id(fds->global_blacklist_bloom_fd, fds->inactive_slot,
			   &inactive_id) != 0 ||
	    active_id == inactive_id ||
	    apply_outer_id(fds->global_blacklist_lpm_fd, fds->active_slot,
			   &active_id) != 0 ||
	    apply_outer_id(fds->global_blacklist_lpm_fd, fds->inactive_slot,
			   &inactive_id) != 0 ||
	    active_id == inactive_id ||
	    bpf_map_lookup_elem(fds->fair_node_config_fd, &fds->active_slot,
				&active_node) != 0 ||
	    bpf_map_lookup_elem(fds->fair_node_config_fd, &fds->inactive_slot,
				&inactive_node) != 0 ||
	    memcmp(&active_node, &inactive_node, sizeof(active_node)) != 0 ||
	    bpf_map_lookup_elem(fds->gbl_meta_fd, &fds->inactive_slot,
				&inactive_meta) != 0 ||
	    inactive_meta.flags != expected_flags)
		goto out;
	lpm_fd = apply_inner_fd(fds->global_blacklist_lpm_fd, fds->inactive_slot);
	if (lpm_fd < 0 || verify_global_lpm(lpm_fd, fds->node) != 0)
		goto out;

#ifdef XDPGW_APPLY_TEST
	if (apply_test_fault == APPLY_TEST_FAULT_VERIFY_MISMATCH)
		goto out;
#endif
	err = 0;
out:
	if (lpm_fd >= 0)
		close(lpm_fd);
	return err;
}

/* The sole active_config mutation in the core: publish the fully verified
 * inactive slot and advance the node-global version together. */
static inline int commit(struct apply_fds *fds)
{
	struct active_config active;
	uint32_t zero = 0;

	if (!fds)
		return -1;
	active.active_slot = fds->inactive_slot;
	active.version = fds->version + 1;
	if (bpf_map_update_elem(fds->active_config_fd, &zero, &active,
				BPF_ANY) != 0) {
		fprintf(stderr, "xdpgw-apply: commit active_config: %s\n",
			strerror(errno));
		return -1;
	}
	return 0;
}

/* A failed apply always returns before the sole active_config update. Each
 * stage owns and closes its transient fds before returning, so fail only has
 * to preserve the no-commit invariant. */
static inline int apply_node_cfg(struct apply_fds *fds,
				 const struct node_cfg *node)
{
	if (build_inactive_slot(fds, node) != 0)
		goto fail;
	if (carry_forward_feed(fds) != 0)
		goto fail;
	if (verify_slot(fds) != 0)
		goto fail;
	if (commit(fds) != 0)
		goto fail;
	return 0;

fail:
	return -1;
}

static inline int apply_global_deny_cfg(struct apply_fds *fds,
					const struct node_cfg *node)
{
	if (apply_read_active(fds) != 0)
		goto fail;
	if (carry_forward_service_config(fds) != 0)
		goto fail;
	if (build_global_deny_slot(fds, node) != 0)
		goto fail;
	if (verify_global_deny_slot(fds) != 0)
		goto fail;
	if (commit(fds) != 0)
		goto fail;
	return 0;

fail:
	return -1;
}

#ifndef XDPGW_APPLY_NO_MAIN
static void init_apply_fds(struct apply_fds *fds)
{
	*fds = (struct apply_fds){
		.active_config_fd = -1,
		.service_map_fd = -1,
		.rule_block_map_fd = -1,
		.whitelist_bloom_fd = -1,
		.whitelist_lpm_fd = -1,
		.vip_config_map_fd = -1,
		.global_blacklist_bloom_fd = -1,
		.global_blacklist_lpm_fd = -1,
		.udp_blocked_port_bitmap_fd = -1,
		.fair_config_map_fd = -1,
		.fair_node_config_fd = -1,
		.gbl_meta_fd = -1,
	};
}

static void close_apply_fds(struct apply_fds *fds)
{
	int *map_fds[] = {
		&fds->active_config_fd,
		&fds->service_map_fd,
		&fds->rule_block_map_fd,
		&fds->whitelist_bloom_fd,
		&fds->whitelist_lpm_fd,
		&fds->vip_config_map_fd,
		&fds->global_blacklist_bloom_fd,
		&fds->global_blacklist_lpm_fd,
		&fds->udp_blocked_port_bitmap_fd,
		&fds->fair_config_map_fd,
		&fds->fair_node_config_fd,
		&fds->gbl_meta_fd,
	};
	size_t i;

	for (i = 0; i < sizeof(map_fds) / sizeof(map_fds[0]); i++) {
		if (*map_fds[i] >= 0) {
			close(*map_fds[i]);
			*map_fds[i] = -1;
		}
	}
}

static int open_apply_pin(const char *name, const char *path, int *fd)
{
	*fd = bpf_obj_get(path);
	if (*fd >= 0)
		return 0;

	fprintf(stderr,
		"xdpgw-apply: required pinned config map %s is unavailable at %s: %s; "
		"is xdp_gateway_loader running?\n",
		name, path, strerror(errno));
	return -1;
}

static int open_apply_pins(struct apply_fds *fds)
{
	init_apply_fds(fds);

	if (open_apply_pin("active_config", APPLY_ACTIVE_CONFIG_PIN,
			   &fds->active_config_fd) != 0 ||
	    open_apply_pin("service_map", APPLY_SERVICE_MAP_PIN,
			   &fds->service_map_fd) != 0 ||
	    open_apply_pin("rule_block_map", APPLY_RULE_BLOCK_MAP_PIN,
			   &fds->rule_block_map_fd) != 0 ||
	    open_apply_pin("whitelist_bloom", APPLY_WHITELIST_BLOOM_PIN,
			   &fds->whitelist_bloom_fd) != 0 ||
	    open_apply_pin("whitelist_lpm", APPLY_WHITELIST_LPM_PIN,
			   &fds->whitelist_lpm_fd) != 0 ||
	    open_apply_pin("vip_config_map", APPLY_VIP_CONFIG_MAP_PIN,
			   &fds->vip_config_map_fd) != 0 ||
	    open_apply_pin("global_blacklist_bloom", APPLY_GLOBAL_BLACKLIST_BLOOM_PIN,
			   &fds->global_blacklist_bloom_fd) != 0 ||
	    open_apply_pin("global_blacklist_lpm", APPLY_GLOBAL_BLACKLIST_LPM_PIN,
			   &fds->global_blacklist_lpm_fd) != 0 ||
	    open_apply_pin("udp_blocked_port_bitmap",
			   APPLY_UDP_BLOCKED_PORT_BITMAP_PIN,
			   &fds->udp_blocked_port_bitmap_fd) != 0 ||
	    open_apply_pin("fair_config_map", APPLY_FAIR_CONFIG_MAP_PIN,
			   &fds->fair_config_map_fd) != 0 ||
	    open_apply_pin("fair_node_config", APPLY_FAIR_NODE_CONFIG_PIN,
			   &fds->fair_node_config_fd) != 0 ||
	    open_apply_pin("gbl_meta", APPLY_GBL_META_PIN, &fds->gbl_meta_fd) != 0)
		goto fail;

	return 0;

fail:
	close_apply_fds(fds);
	return -1;
}

static int lock_apply_pin_dir(void)
{
	int fd = open(APPLY_PIN_DIR, O_RDONLY | O_DIRECTORY | O_CLOEXEC);

	if (fd < 0) {
		fprintf(stderr, "xdpgw-apply: open pin directory %s: %s\n",
			APPLY_PIN_DIR, strerror(errno));
		return -1;
	}
	if (flock(fd, LOCK_EX) != 0) {
		fprintf(stderr, "xdpgw-apply: lock pin directory %s: %s\n",
			APPLY_PIN_DIR, strerror(errno));
		close(fd);
		return -1;
	}
	return fd;
}

int main(int argc, char **argv)
{
	struct node_cfg node;
	struct apply_fds fds;
	int lock_fd = -1;
	int err = 1;

	if (argc != 2) {
		fprintf(stderr, "usage: %s <snapshot-path>\n", argv[0]);
		return 2;
	}

	if (parse_snapshot(argv[1], &node) != 0) {
		fprintf(stderr, "xdpgw-apply: failed to parse snapshot %s\n", argv[1]);
		return 1;
	}
	init_apply_fds(&fds);
	lock_fd = lock_apply_pin_dir();
	if (lock_fd < 0)
		goto out;
	if (open_apply_pins(&fds) != 0)
		goto out;
	if (node.snapshot_kind == APPLY_SNAPSHOT_KIND_SERVICE_FULL) {
		if (apply_node_cfg(&fds, &node) == 0)
			goto committed;
	} else if (node.snapshot_kind == APPLY_SNAPSHOT_KIND_GLOBAL_DENY) {
		if (apply_global_deny_cfg(&fds, &node) == 0)
			goto committed;
	} else {
		errno = EINVAL;
	}
	{
		fprintf(stderr,
			"xdpgw-apply: build, verify, or carry-forward failed before "
			"active_config swap%s%s\n",
			errno ? ": " : "",
			errno ? strerror(errno) : "");
		goto out;
	}


committed:
	if (node.snapshot_kind == APPLY_SNAPSHOT_KIND_GLOBAL_DENY) {
		printf("{\"active_slot\":%u,\"node_map_version\":%u}\n",
		       fds.inactive_slot, fds.version + 1);
		err = 0;
		goto out;
	}
	printf("xdpgw-apply: swapped active slot %u to %u, version %u to %u "
	       "(%u service(s))\n",
	       fds.active_slot, fds.inactive_slot, fds.version, fds.version + 1,
	       node.service_count);
	err = 0;

out:
	close_apply_fds(&fds);
	if (lock_fd >= 0)
		close(lock_fd);
	free_node_cfg(&node);
	return err;
}
#endif
