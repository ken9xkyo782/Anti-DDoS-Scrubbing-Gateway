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
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
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
	uint8_t bl_flags;
	uint8_t vip_flags;
	uint64_t committed_bps;
	uint64_t ceiling_bps;
	uint64_t vip_pps;
	uint64_t vip_bps;
	uint16_t rule_count;
	struct cfg_rule rules[RULE_MAX];
	uint32_t wl_count;
	struct cfg_source *wl;
	uint32_t sbl_count;
	struct cfg_source *sbl;
};

struct node_cfg {
	uint32_t schema_version;
	uint32_t service_count;
	struct cfg_service *services;
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
	int service_blacklist_bloom_fd;
	int service_blacklist_lpm_fd;
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
	APPLY_SERVICE_BLACKLIST_BLOOM,
	APPLY_SERVICE_BLACKLIST_LPM,
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
	if (!node || !node->services) {
		if (node) {
			node->services = NULL;
			node->service_count = 0;
		}
		return;
	}
	for (uint32_t i = 0; i < node->service_count; i++) {
		free(node->services[i].wl);
		free(node->services[i].sbl);
	}
	free(node->services);
	node->services = NULL;
	node->service_count = 0;
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
	    rd_u8(c, &svc->bl_flags) != 0 ||
	    rd_u64le(c, &svc->committed_bps) != 0 ||
	    rd_u64le(c, &svc->ceiling_bps) != 0 ||
	    rd_u64le(c, &svc->vip_pps) != 0 ||
	    rd_u64le(c, &svc->vip_bps) != 0 ||
	    rd_u8(c, &svc->vip_flags) != 0 ||
	    rd_u16le(c, &rule_count) != 0)
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
	if (parse_source_list(c, &svc->sbl_count, &svc->sbl) != 0)
		return -1;

	return 0;
}

/*
 * parse_snapshot — decode the wire snapshot at `path` into `out`.
 * Fail-closed: rejects a bad magic, an unknown schema_version, an out-of-bounds
 * count, or any truncation before returning success. On failure `out` is left
 * empty and any partial allocation is released; on success the caller owns the
 * result and must free_node_cfg() it.
 */
static inline int parse_snapshot(const char *path, struct node_cfg *out)
{
	uint8_t magic[APPLY_SNAPSHOT_MAGIC_SIZE];
	uint8_t *buf = NULL;
	struct rdcur c;
	long size;
	size_t got;
	uint32_t schema = 0;
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
	if (rd_u32le(&c, &count) != 0 || count > APPLY_MAX_SERVICES) {
		fprintf(stderr, "xdpgw-apply: %s service_count %u out of range\n",
			path, count);
		free(buf);
		return -1;
	}

	out->schema_version = schema;
	if (count > 0) {
		out->services = calloc(count, sizeof(*out->services));
		if (!out->services) {
			free(buf);
			return -1;
		}
	}

	for (uint32_t i = 0; i < count; i++) {
		if (parse_service(&c, &out->services[i]) != 0) {
			fprintf(stderr,
				"xdpgw-apply: %s truncated/invalid at service %u\n",
				path, i);
			out->service_count = i; /* free what we built */
			free_node_cfg(out);
			free(buf);
			return -1;
		}
		out->service_count = i + 1;
	}

	free(buf);
	return 0;
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
					     int sbl_bloom_fd, int sbl_lpm_fd,
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
		.bl_flags = service->bl_flags,
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
	    service->enabled > 1 || service->rule_count > RULE_MAX ||
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

	for (i = 0; i < service->sbl_count; i++) {
		const struct cfg_source *source = &service->sbl[i];
		struct sbl_lpm_key lpm_key = {
			.prefixlen = 32 + source->prefixlen,
			.service_id = htonl(service_id),
			.src = source->addr,
		};
		__u8 present = 1;

		if (source->prefixlen > 32) {
			fprintf(stderr, "xdpgw-apply: invalid service blacklist prefix\n");
			return -1;
		}
		if (source->prefixlen >= SBL_BLOOM_PREFIX) {
			struct sbl_bloom_key bloom_key = {
				.service_id = htonl(service_id),
				.src24 = htonl(ntohl(source->addr) & BL_SRC24_MASK),
			};

			if (bpf_map_update_elem(sbl_bloom_fd, NULL, &bloom_key,
						BPF_ANY) != 0)
				return -1;
		}
		if (bpf_map_update_elem(sbl_lpm_fd, &lpm_key, &present, BPF_ANY) != 0)
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
	outers[APPLY_SERVICE_BLACKLIST_BLOOM] = fds->service_blacklist_bloom_fd;
	outers[APPLY_SERVICE_BLACKLIST_LPM] = fds->service_blacklist_lpm_fd;
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
					fresh[APPLY_SERVICE_BLACKLIST_BLOOM],
					fresh[APPLY_SERVICE_BLACKLIST_LPM],
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
	outers[APPLY_SERVICE_BLACKLIST_BLOOM] = fds->service_blacklist_bloom_fd;
	outers[APPLY_SERVICE_BLACKLIST_LPM] = fds->service_blacklist_lpm_fd;
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
		    value.bl_flags != service->bl_flags ||
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

#ifndef XDPGW_APPLY_NO_MAIN
int main(int argc, char **argv)
{
	struct node_cfg node;

	if (argc < 2) {
		fprintf(stderr, "usage: %s <snapshot-path>\n", argv[0]);
		return 2;
	}

	if (parse_snapshot(argv[1], &node) != 0)
		return 1;

	/*
	 * T3 provides the fd-taking build/verify/commit core above. T5 opens the
	 * pinned maps and invokes it; until then this CLI must remain fail-closed.
	 */
	fprintf(stderr,
		"xdpgw-apply: parsed %u service(s) (schema v%u); pin-open CLI not yet implemented (T5)\n",
		node.service_count, node.schema_version);
	free_node_cfg(&node);
	return 3;
}
#endif
