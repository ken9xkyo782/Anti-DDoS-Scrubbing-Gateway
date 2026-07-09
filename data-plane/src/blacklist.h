#ifndef XDP_GATEWAY_BLACKLIST_H
#define XDP_GATEWAY_BLACKLIST_H

#include <linux/types.h>

#include "pkt_meta.h"
#include "service.h"

#define GBL_BLOOM_PREFIX 24
#define GBL_EXPAND_FLOOR 16
#define GBL_BLOOM_MAX_ENTRIES (2u * 1024 * 1024)
#define GBL_BLOOM_HASHES 5
#define GBL_LPM_MAX_ENTRIES (1024 * 1024)
#define SBL_BLOOM_PREFIX 24
#define SBL_BLOOM_MAX_ENTRIES 65536
#define SBL_BLOOM_HASHES 5
#define SBL_LPM_MAX_ENTRIES 65536
#define BLOCKED_PORT_WORDS 1024
#define BL_SRC24_MASK 0xffffff00U

#define BL_STATE_NONE 0
#define BL_STATE_CLEAN 1
#define BL_STATE_AMP_HARDCODED 2
#define BL_STATE_BOGON 3
#define BL_STATE_AMP_BITMAP 4
#define BL_STATE_GLOBAL_HIT 5
#define BL_STATE_SERVICE_HIT 6

enum gbl_flags {
	GBL_F_ACTIVE = 1 << 0,
	GBL_F_HAS_BROAD = 1 << 1,
};

enum bl_service_flags {
	BL_F_ACTIVE = 1 << 0,
	BL_F_HAS_BROAD = 1 << 1,
};

enum bloom_fp_stage {
	BLOOM_FP_WHITELIST = 0,
	BLOOM_FP_GLOBAL = 1,
	BLOOM_FP_SERVICE = 2,
	BLOOM_STAT_MAX = 3,
};

struct bl_lpm_key {
	__u32 prefixlen;
	__be32 src;
};

struct sbl_lpm_key {
	__u32 prefixlen;
	__be32 service_id;
	__be32 src;
};

struct sbl_bloom_key {
	__be32 service_id;
	__be32 src24;
};

struct gbl_meta {
	__u8 flags;
	__u8 _pad[3];
};

_Static_assert(sizeof(struct bl_lpm_key) == 8,
	       "bl_lpm_key size is part of the M4 map contract");
_Static_assert(sizeof(struct sbl_lpm_key) == 12,
	       "sbl_lpm_key size is part of the M4 map contract");
_Static_assert(sizeof(struct sbl_bloom_key) == 8,
	       "sbl_bloom_key size is part of the M4 map contract");
_Static_assert(sizeof(struct gbl_meta) == 4,
	       "gbl_meta size is part of the M4 map contract");

/*
 * M4 build contract:
 * - All blacklist config maps are emitted from one snapshot and selected by
 *   the active slot pinned at ingress.
 * - Bloom inners are replace-only. Build fresh inners and swap them into the
 *   inactive slot; replacements must keep value_size/max_entries/map_extra
 *   equal to these definitions.
 * - Bloom contents must be a per-slot superset of LPM entries. The
 *   no-false-negative property is a builder invariant.
 * - Global bloom keys are /24 buckets. Prefixes 16..23 are expanded by the
 *   builder into their covered /24 keys; prefixes below 16, or snapshots that
 *   would over-fill the bloom, set GBL_F_HAS_BROAD instead of over-filling.
 * - Service blacklist keys are scoped by service_id and use the same /24 bloom
 *   bucket shape as AD-021 whitelist keys.
 * - GBL_F_ACTIVE/BL_F_ACTIVE are unset for empty scopes. Disabled or expired
 *   rows are omitted from both bloom and LPM maps.
 */

#ifdef __BPF__
#include <linux/bpf.h>
#include <bpf/bpf_endian.h>
#include <bpf/bpf_helpers.h>

struct gbl_bloom_inner_map_def {
	__uint(type, BPF_MAP_TYPE_BLOOM_FILTER);
	__uint(max_entries, GBL_BLOOM_MAX_ENTRIES);
	__uint(map_extra, GBL_BLOOM_HASHES);
	__type(value, __be32);
};

struct gbl_bloom_inner_map_def global_blacklist_bloom_0 SEC(".maps");
struct gbl_bloom_inner_map_def global_blacklist_bloom_1 SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY_OF_MAPS);
	__uint(max_entries, SERVICE_SLOTS);
	__type(key, __u32);
	__array(values, struct gbl_bloom_inner_map_def);
} global_blacklist_bloom SEC(".maps") = {
	.values = {
		[0] = &global_blacklist_bloom_0,
		[1] = &global_blacklist_bloom_1,
	},
};

struct gbl_lpm_inner_map_def {
	__uint(type, BPF_MAP_TYPE_LPM_TRIE);
	__uint(max_entries, GBL_LPM_MAX_ENTRIES);
	__uint(map_flags, BPF_F_NO_PREALLOC);
	__type(key, struct bl_lpm_key);
	__type(value, __u8);
};

struct gbl_lpm_inner_map_def global_blacklist_lpm_0 SEC(".maps");
struct gbl_lpm_inner_map_def global_blacklist_lpm_1 SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY_OF_MAPS);
	__uint(max_entries, SERVICE_SLOTS);
	__type(key, __u32);
	__array(values, struct gbl_lpm_inner_map_def);
} global_blacklist_lpm SEC(".maps") = {
	.values = {
		[0] = &global_blacklist_lpm_0,
		[1] = &global_blacklist_lpm_1,
	},
};

struct sbl_bloom_inner_map_def {
	__uint(type, BPF_MAP_TYPE_BLOOM_FILTER);
	__uint(max_entries, SBL_BLOOM_MAX_ENTRIES);
	__uint(map_extra, SBL_BLOOM_HASHES);
	__type(value, struct sbl_bloom_key);
};

struct sbl_bloom_inner_map_def service_blacklist_bloom_0 SEC(".maps");
struct sbl_bloom_inner_map_def service_blacklist_bloom_1 SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY_OF_MAPS);
	__uint(max_entries, SERVICE_SLOTS);
	__type(key, __u32);
	__array(values, struct sbl_bloom_inner_map_def);
} service_blacklist_bloom SEC(".maps") = {
	.values = {
		[0] = &service_blacklist_bloom_0,
		[1] = &service_blacklist_bloom_1,
	},
};

struct sbl_lpm_inner_map_def {
	__uint(type, BPF_MAP_TYPE_LPM_TRIE);
	__uint(max_entries, SBL_LPM_MAX_ENTRIES);
	__uint(map_flags, BPF_F_NO_PREALLOC);
	__type(key, struct sbl_lpm_key);
	__type(value, __u8);
};

struct sbl_lpm_inner_map_def service_blacklist_lpm_0 SEC(".maps");
struct sbl_lpm_inner_map_def service_blacklist_lpm_1 SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY_OF_MAPS);
	__uint(max_entries, SERVICE_SLOTS);
	__type(key, __u32);
	__array(values, struct sbl_lpm_inner_map_def);
} service_blacklist_lpm SEC(".maps") = {
	.values = {
		[0] = &service_blacklist_lpm_0,
		[1] = &service_blacklist_lpm_1,
	},
};

struct blocked_port_bitmap_inner_map_def {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, BLOCKED_PORT_WORDS);
	__type(key, __u32);
	__type(value, __u64);
};

struct blocked_port_bitmap_inner_map_def udp_blocked_port_bitmap_0 SEC(".maps");
struct blocked_port_bitmap_inner_map_def udp_blocked_port_bitmap_1 SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY_OF_MAPS);
	__uint(max_entries, SERVICE_SLOTS);
	__type(key, __u32);
	__array(values, struct blocked_port_bitmap_inner_map_def);
} udp_blocked_port_bitmap SEC(".maps") = {
	.values = {
		[0] = &udp_blocked_port_bitmap_0,
		[1] = &udp_blocked_port_bitmap_1,
	},
};

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, SERVICE_SLOTS);
	__type(key, __u32);
	__type(value, struct gbl_meta);
} gbl_meta SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
	__uint(max_entries, BLOOM_STAT_MAX);
	__type(key, __u32);
	__type(value, __u64);
} bloom_stats SEC(".maps");

static __always_inline void bump_bloom_fp(enum bloom_fp_stage stage)
{
	__u32 key = (__u32)stage;
	__u64 *count = bpf_map_lookup_elem(&bloom_stats, &key);

	if (count)
		__sync_fetch_and_add(count, 1);
}

static __always_inline int amp_port_hardcoded(__u16 sport_host)
{
	switch (sport_host) {
	case 17:
	case 19:
	case 53:
	case 111:
	case 123:
	case 137:
	case 161:
	case 389:
	case 520:
	case 1900:
	case 5353:
	case 11211:
		return 1;
	default:
		return 0;
	}
}

static __always_inline int bogon_src(__be32 saddr_be)
{
	__u32 src = bpf_ntohl(saddr_be);

	if ((src & 0xff000000U) == 0x00000000U)
		return 1;
	if ((src & 0xff000000U) == 0x0a000000U)
		return 1;
	if ((src & 0xffc00000U) == 0x64400000U)
		return 1;
	if ((src & 0xff000000U) == 0x7f000000U)
		return 1;
	if ((src & 0xffff0000U) == 0xa9fe0000U)
		return 1;
	if ((src & 0xfff00000U) == 0xac100000U)
		return 1;
	if ((src & 0xffffff00U) == 0xc0000000U)
		return 1;
	if ((src & 0xffffff00U) == 0xc0000200U)
		return 1;
	if ((src & 0xffff0000U) == 0xc0a80000U)
		return 1;
	if ((src & 0xfffe0000U) == 0xc6120000U)
		return 1;
	if ((src & 0xffffff00U) == 0xc6336400U)
		return 1;
	if ((src & 0xffffff00U) == 0xcb007100U)
		return 1;
	if ((src & 0xf0000000U) == 0xe0000000U)
		return 1;
	if ((src & 0xf0000000U) == 0xf0000000U)
		return 1;
	return 0;
}

#endif /* __BPF__ */

#endif
