#ifndef XDP_GATEWAY_WHITELIST_H
#define XDP_GATEWAY_WHITELIST_H

#include <linux/types.h>

#include "pkt_meta.h"
#include "rules.h"
#include "service.h"

#define WL_BLOOM_PREFIX 24
#define WL_BLOOM_MAX_ENTRIES 65536
#define WL_BLOOM_HASHES 5
#define WL_LPM_MAX_ENTRIES 65536
#define VIP_CONFIG_MAX_ENTRIES 1024
#define VIP_CEILING_STATE_MAX_ENTRIES 1024

#define WL_STATE_NONE 0
#define WL_STATE_MISS 1
#define WL_STATE_HIT_ADMIT 2
#define WL_STATE_HIT_DROP 3

#define WL_TEST_BLOOM_SERVICE_ID 0x01020304U
#define WL_TEST_BLOOM_PRESENT_SRC24 0xc6336400U
#define WL_TEST_BLOOM_ABSENT_SRC24 0xcb007100U

enum wl_service_flags {
	WL_F_ACTIVE = 1 << 0,
	WL_F_HAS_BROAD = 1 << 1,
};

enum vip_flags {
	VIP_F_PPS_SET = 1 << 0,
	VIP_F_BPS_SET = 1 << 1,
};

struct wl_lpm_key {
	__u32 prefixlen;
	__be32 service_id;
	__be32 src;
};

struct wl_bloom_key {
	__be32 service_id;
	__be32 src24;
};

struct vip_config {
	__u32 version;
	__u8 flags;
	__u8 _pad[3];
	__u64 pps;
	__u64 bps; /* bytes/sec; M4 converts from the control-plane unit. */
};

_Static_assert(sizeof(struct wl_bloom_key) == 8,
	       "wl_bloom_key size is part of the M4 map contract");
_Static_assert(sizeof(struct vip_config) == 24,
	       "vip_config size is part of the M4 map contract");

/*
 * M4 build contract:
 * - Bloom inners are replace-only. Build fresh inners and swap them into the
 *   inactive slot; replacements must keep value_size/max_entries/map_extra
 *   equal to these definitions.
 * - Bloom contents must be a per-slot superset of the LPM entries. The
 *   no-false-negative property is a builder invariant.
 * - WL_F_ACTIVE/WL_F_HAS_BROAD are emitted from the same snapshot as the
 *   whitelist inners; both swap atomically through the active slot.
 * - D-WLV-1: entries with both VIP dimensions NULL leave WL_F_ACTIVE unset,
 *   omit vip_config, and may omit whitelist entries to save map space.
 */

#ifdef __BPF__
#include <linux/bpf.h>
#include <linux/errno.h>
#include <bpf/bpf_endian.h>
#include <bpf/bpf_helpers.h>

struct wl_bloom_inner_map_def {
	__uint(type, BPF_MAP_TYPE_BLOOM_FILTER);
	__uint(max_entries, WL_BLOOM_MAX_ENTRIES);
	__uint(map_extra, WL_BLOOM_HASHES);
	__type(value, struct wl_bloom_key);
};

struct wl_bloom_inner_map_def whitelist_bloom_0 SEC(".maps");
struct wl_bloom_inner_map_def whitelist_bloom_1 SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY_OF_MAPS);
	__uint(max_entries, SERVICE_SLOTS);
	__type(key, __u32);
	__array(values, struct wl_bloom_inner_map_def);
} whitelist_bloom SEC(".maps") = {
	.values = {
		[0] = &whitelist_bloom_0,
		[1] = &whitelist_bloom_1,
	},
};

struct wl_lpm_inner_map_def {
	__uint(type, BPF_MAP_TYPE_LPM_TRIE);
	__uint(max_entries, WL_LPM_MAX_ENTRIES);
	__uint(map_flags, BPF_F_NO_PREALLOC);
	__type(key, struct wl_lpm_key);
	__type(value, __u8);
};

struct wl_lpm_inner_map_def whitelist_lpm_0 SEC(".maps");
struct wl_lpm_inner_map_def whitelist_lpm_1 SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY_OF_MAPS);
	__uint(max_entries, SERVICE_SLOTS);
	__type(key, __u32);
	__array(values, struct wl_lpm_inner_map_def);
} whitelist_lpm SEC(".maps") = {
	.values = {
		[0] = &whitelist_lpm_0,
		[1] = &whitelist_lpm_1,
	},
};

struct vip_config_inner_map_def {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, VIP_CONFIG_MAX_ENTRIES);
	__type(key, __u32);
	__type(value, struct vip_config);
};

struct vip_config_inner_map_def vip_config_0 SEC(".maps");
struct vip_config_inner_map_def vip_config_1 SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY_OF_MAPS);
	__uint(max_entries, SERVICE_SLOTS);
	__type(key, __u32);
	__array(values, struct vip_config_inner_map_def);
} vip_config_map SEC(".maps") = {
	.values = {
		[0] = &vip_config_0,
		[1] = &vip_config_1,
	},
};

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_HASH);
	__uint(max_entries, VIP_CEILING_STATE_MAX_ENTRIES);
	__type(key, __u32);
	__type(value, struct rl_bucket);
} vip_ceiling_state SEC(".maps");

static __always_inline struct wl_bloom_key wl_test_bloom_key(__u32 src24)
{
	struct wl_bloom_key key = {
		.service_id = bpf_htonl(WL_TEST_BLOOM_SERVICE_ID),
		.src24 = bpf_htonl(src24),
	};

	return key;
}

static __always_inline int whitelist_test_bloom_probe(__u32 slot,
						      int expect_present)
{
	struct wl_bloom_key key;
	void *inner = bpf_map_lookup_elem(&whitelist_bloom, &slot);
	long ret;

	if (!inner)
		return -1;

	key = wl_test_bloom_key(expect_present ? WL_TEST_BLOOM_PRESENT_SRC24 :
						 WL_TEST_BLOOM_ABSENT_SRC24);
	ret = bpf_map_peek_elem(inner, &key);
	if (expect_present)
		return ret == 0 ? 0 : -1;
	return ret == -ENOENT ? 0 : -1;
}
#endif

#endif
