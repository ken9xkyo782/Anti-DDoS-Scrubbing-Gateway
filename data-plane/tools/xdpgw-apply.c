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

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <bpf/bpf.h>
#include <bpf/libbpf.h>

#include "apply_snapshot.h"
#include "blacklist.h"
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
	 * T3–T5: open pinned config maps, build_inactive_slot(), verify_slot(),
	 * commit(). Until then the helper only proves the snapshot decodes; it
	 * must not report success, so it exits non-zero (fail-closed).
	 */
	fprintf(stderr,
		"xdpgw-apply: parsed %u service(s) (schema v%u); build/swap not yet implemented (T3)\n",
		node.service_count, node.schema_version);
	free_node_cfg(&node);
	return 3;
}
#endif
