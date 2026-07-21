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
#define WL_SRC24_MASK 0xffffff00U

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

static __always_inline struct wl_bloom_key wl_bloom_key(__u32 service_id,
							__be32 src)
{
	struct wl_bloom_key key = {
		.service_id = bpf_htonl(service_id),
		.src24 = src & bpf_htonl(WL_SRC24_MASK),
	};

	return key;
}

static __always_inline int wl_bloom_maybe(__u32 slot, __u32 service_id,
					  __be32 src, int *maybe)
{
	struct wl_bloom_key key = wl_bloom_key(service_id, src);
	void *inner = bpf_map_lookup_elem(&whitelist_bloom, &slot);
	long ret;

	if (!inner)
		return -1;

	ret = bpf_map_peek_elem(inner, &key);
	if (ret == 0) {
		*maybe = 1;
		return 0;
	}
	if (ret == -ENOENT) {
		*maybe = 0;
		return 0;
	}
	return -1;
}

static __always_inline int wl_lpm_hit(__u32 slot, __u32 service_id, __be32 src,
				      int *hit)
{
	struct wl_lpm_key key = {
		.prefixlen = 64,
		.service_id = bpf_htonl(service_id),
		.src = src,
	};
	void *inner = bpf_map_lookup_elem(&whitelist_lpm, &slot);
	__u8 *present;

	if (!inner)
		return -1;

	present = bpf_map_lookup_elem(inner, &key);
	*hit = present != 0;
	return 0;
}

static __always_inline struct vip_config *vip_config_lookup(__u32 slot,
							    __u32 service_id)
{
	void *inner = bpf_map_lookup_elem(&vip_config_map, &slot);

	if (!inner)
		return 0;
	return bpf_map_lookup_elem(inner, &service_id);
}

static __always_inline void vip_bucket_reset(struct rl_bucket *bucket,
					     const struct vip_config *config,
					     __u64 now, __u32 ncpus,
					     int test_no_refill)
{
	bucket->cfg_version = config->version;
	bucket->_pad = 0;
	bucket->last_ns = now;
	bucket->pps_tokens = rl_burst(config->pps, ncpus, test_no_refill);
	bucket->bps_tokens = rl_burst(config->bps, ncpus, test_no_refill);
}

static __always_inline void vip_bucket_refill(struct rl_bucket *bucket,
					      const struct vip_config *config,
					      __u64 now, __u32 ncpus)
{
	__u64 pps_burst;
	__u64 bps_burst;
	__u64 elapsed;
	__u64 advance = 0;
	__u64 dim_advance;

	if (now <= bucket->last_ns)
		return;

	elapsed = now - bucket->last_ns;
	if (elapsed > NSEC_PER_SEC)
		elapsed = NSEC_PER_SEC;

	if (config->flags & VIP_F_PPS_SET) {
		pps_burst = rl_burst(config->pps, ncpus, 0);
		dim_advance = rl_refill_dim(&bucket->pps_tokens, config->pps,
					    pps_burst, elapsed, ncpus);
		if (dim_advance > advance)
			advance = dim_advance;
	}

	if (config->flags & VIP_F_BPS_SET) {
		bps_burst = rl_burst(config->bps, ncpus, 0);
		dim_advance = rl_refill_dim(&bucket->bps_tokens, config->bps,
					    bps_burst, elapsed, ncpus);
		if (dim_advance > advance)
			advance = dim_advance;
	}

	if (advance > 0)
		bucket->last_ns += advance;
}

static __always_inline int vip_bucket_consume(struct rl_bucket *bucket,
					      const struct vip_config *config,
					      __u64 pkt_len)
{
	return rl_bucket_consume_raw(bucket,
				     !!(config->flags & VIP_F_PPS_SET),
				     !!(config->flags & VIP_F_BPS_SET),
				     pkt_len);
}

static __always_inline int vip_bucket_admit(const struct vip_config *config,
					    const struct pkt_meta *meta,
					    __u64 pkt_len)
{
	__u32 key = meta->service_id;
	struct rl_bucket fresh = {};
	struct rl_bucket *bucket;
	__u32 ncpus;
	__u64 now;
	int test_no_refill;
	int admitted;

	if (!(config->flags & (VIP_F_PPS_SET | VIP_F_BPS_SET)))
		return 1;

	ncpus = rl_cpu_count();
	test_no_refill = rl_test_no_refill();
	now = bpf_ktime_get_ns();
	bucket = bpf_map_lookup_elem(&vip_ceiling_state, &key);
	if (!bucket) {
		vip_bucket_reset(&fresh, config, now, ncpus, test_no_refill);
		admitted = vip_bucket_consume(&fresh, config, pkt_len);
		if (bpf_map_update_elem(&vip_ceiling_state, &key, &fresh,
					BPF_ANY) != 0)
			return -1;
		return admitted;
	}

	if (bucket->cfg_version != config->version)
		vip_bucket_reset(bucket, config, now, ncpus, test_no_refill);
	else if (!test_no_refill)
		vip_bucket_refill(bucket, config, now, ncpus);

	return vip_bucket_consume(bucket, config, pkt_len);
}

static __always_inline int whitelist_miss(struct xdp_md *ctx,
					  struct pkt_meta *meta, __u32 slot,
					  __u8 bl_flags, int record_state)
{
	if (record_state) {
		meta->wl_state = WL_STATE_MISS;
		write_test_meta(meta);
	}
	return deny_filter_stage(ctx, meta, slot, bl_flags);
}

static __always_inline int whitelist_stage(struct xdp_md *ctx,
					   struct pkt_meta *meta, __u32 slot,
					   const struct service_val *service)
{
	void *data = (void *)(long)ctx->data;
	void *data_end = (void *)(long)ctx->data_end;
	struct vip_config *config;
	__u64 pkt_len = data_end - data;
	__u8 wl_flags = service->wl_flags;
	__u8 bl_flags = service->bl_flags;
	int bloom_consulted = 0;
	int admitted;
	int maybe = 1;
	int hit = 0;

	if (!(wl_flags & WL_F_ACTIVE))
		return whitelist_miss(ctx, meta, slot, bl_flags, 0);

	if (!(wl_flags & WL_F_HAS_BROAD)) {
		if (wl_bloom_maybe(slot, meta->service_id, meta->src_ip,
				   &maybe) != 0)
			return record_drop(meta, DR_MAP_ERROR);
		if (!maybe)
			return whitelist_miss(ctx, meta, slot, bl_flags, 1);
		bloom_consulted = 1;
	}

	if (wl_lpm_hit(slot, meta->service_id, meta->src_ip, &hit) != 0)
		return record_drop(meta, DR_MAP_ERROR);
	if (!hit) {
		if (bloom_consulted)
			bump_bloom_fp(BLOOM_FP_WHITELIST);
		return whitelist_miss(ctx, meta, slot, bl_flags, 1);
	}

	config = vip_config_lookup(slot, meta->service_id);
	if (!config)
		return record_drop(meta, DR_MAP_ERROR);
	if (!(config->flags & (VIP_F_PPS_SET | VIP_F_BPS_SET)))
		return whitelist_miss(ctx, meta, slot, bl_flags, 1);

	admitted = vip_bucket_admit(config, meta, pkt_len);
	if (admitted < 0)
		return record_drop(meta, DR_MAP_ERROR);
	if (!admitted) {
		meta->wl_state = WL_STATE_HIT_DROP;
		meta->rule_idx = RULE_IDX_NONE;
		write_test_meta(meta);
		return record_drop(meta, DR_VIP_CEILING_DROP);
	}

	meta->wl_state = WL_STATE_HIT_ADMIT;
	meta->rule_idx = RULE_IDX_NONE;
	return redirect_out(ctx, meta);
}
#endif

#endif
