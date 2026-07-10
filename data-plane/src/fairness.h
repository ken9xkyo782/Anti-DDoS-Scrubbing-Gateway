#ifndef XDP_GATEWAY_FAIRNESS_H
#define XDP_GATEWAY_FAIRNESS_H

#include <linux/bpf.h>
#include <linux/types.h>

#include "pkt_meta.h"
#include "rules.h"

#define FAIR_CONFIG_MAX_ENTRIES 1024
#define FAIR_RATE_MAX 16000000000ULL
#define FAIR_CONTINUE (-1)

enum fair_state {
	FAIR_NONE = 0,
	FAIR_CAP_DROP,
	FAIR_COMMITTED,
	FAIR_BURST,
	FAIR_CEILING_DROP,
	FAIR_CONGESTION_DROP,
	FAIR_ERR,
};

struct fair_config {
	__u32 version;
	__u32 _pad;
	__u64 committed_bps;
	__u64 burst_bps;
	__u64 cap_bps;
	__u64 cap_pps;
};

struct fair_node_config {
	__u32 version;
	__u32 _pad;
	__u64 headroom_bps;
};

struct fair_committed_bucket {
	struct bpf_spin_lock lock;
	__u32 cfg_version;
	__u64 tokens;
	__u64 last_ns;
};

_Static_assert(sizeof(struct fair_config) == 40,
	       "fair_config size is part of the M4 map contract");
_Static_assert(sizeof(struct fair_node_config) == 16,
	       "fair_node_config size is part of the M4 map contract");
_Static_assert(sizeof(struct fair_committed_bucket) == 24,
	       "fair_committed_bucket size is part of the runtime map contract");

#define FAIR_TEST_TRIGGER_SPIN_LOCK 4
#define FAIR_TEST_LOCK_SERVICE_ID 0xfa170001U

#ifdef __BPF__
#include <bpf/bpf_helpers.h>

struct fair_config_inner_map_def {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, FAIR_CONFIG_MAX_ENTRIES);
	__type(key, __u32);
	__type(value, struct fair_config);
};

struct fair_config_inner_map_def fair_config_0 SEC(".maps");
struct fair_config_inner_map_def fair_config_1 SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY_OF_MAPS);
	__uint(max_entries, SERVICE_SLOTS);
	__type(key, __u32);
	__array(values, struct fair_config_inner_map_def);
} fair_config_map SEC(".maps") = {
	.values = {
		[0] = &fair_config_0,
		[1] = &fair_config_1,
	},
};

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, SERVICE_SLOTS);
	__type(key, __u32);
	__type(value, struct fair_node_config);
} fair_node_config SEC(".maps");

/* Top-level HASH is required: bpf_spin_lock cannot live in an inner map. */
struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, FAIR_CONFIG_MAX_ENTRIES);
	__type(key, __u32);
	__type(value, struct fair_committed_bucket);
} svc_committed_state SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_HASH);
	__uint(max_entries, FAIR_CONFIG_MAX_ENTRIES);
	__type(key, __u32);
	__type(value, struct rl_bucket);
} svc_burst_state SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
	__uint(max_entries, 1);
	__type(key, __u32);
	__type(value, struct rl_bucket);
} node_burst_state SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_HASH);
	__uint(max_entries, FAIR_CONFIG_MAX_ENTRIES);
	__type(key, __u32);
	__type(value, struct rl_bucket);
} service_ingress_cap_state SEC(".maps");

#ifdef PKT_TEST_HOOKS
static __always_inline int fair_test_spin_lock_mutate(void)
{
	__u32 key = FAIR_TEST_LOCK_SERVICE_ID;
	struct fair_committed_bucket *bucket;
	__u64 now;

	bucket = bpf_map_lookup_elem(&svc_committed_state, &key);
	if (!bucket)
		return -1;

	now = bpf_ktime_get_ns();
	bpf_spin_lock(&bucket->lock);
	bucket->tokens++;
	bucket->last_ns = now;
	bpf_spin_unlock(&bucket->lock);
	return 0;
}
#endif

static __always_inline struct fair_config *fair_config_lookup(__u32 slot,
						       __u32 service_id)
{
	void *inner = bpf_map_lookup_elem(&fair_config_map, &slot);

	if (!inner)
		return 0;
	return bpf_map_lookup_elem(inner, &service_id);
}

static __always_inline void fair_cap_bucket_reset(struct rl_bucket *bucket,
						   const struct fair_config *config,
						   __u64 now, __u32 ncpus,
						   int test_no_refill)
{
	bucket->cfg_version = config->version;
	bucket->_pad = 0;
	bucket->last_ns = now;
	bucket->pps_tokens = rl_burst(config->cap_pps, ncpus, test_no_refill);
	bucket->bps_tokens = rl_burst(config->cap_bps, ncpus, test_no_refill);
}

static __always_inline void fair_cap_bucket_refill(struct rl_bucket *bucket,
						    const struct fair_config *config,
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

	pps_burst = rl_burst(config->cap_pps, ncpus, 0);
	dim_advance = rl_refill_dim(&bucket->pps_tokens, config->cap_pps,
				    pps_burst, elapsed, ncpus);
	if (dim_advance > advance)
		advance = dim_advance;

	bps_burst = rl_burst(config->cap_bps, ncpus, 0);
	dim_advance = rl_refill_dim(&bucket->bps_tokens, config->cap_bps,
				    bps_burst, elapsed, ncpus);
	if (dim_advance > advance)
		advance = dim_advance;

	if (advance > 0)
		bucket->last_ns += advance;
}

static __always_inline int fair_cap_bucket_consume(struct rl_bucket *bucket,
						     const struct fair_config *config,
						     __u64 pkt_len)
{
	struct rule_entry cap = {
		.pps = config->cap_pps,
		.bps = config->cap_bps,
		.flags = RULE_F_PPS_SET | RULE_F_BPS_SET,
	};

	return rl_bucket_consume(bucket, &cap, pkt_len);
}

static __always_inline int fair_cap_admit(const struct fair_config *config,
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

	ncpus = rl_cpu_count();
	test_no_refill = rl_test_no_refill();
	now = bpf_ktime_get_ns();
	bucket = bpf_map_lookup_elem(&service_ingress_cap_state, &key);
	if (!bucket) {
		fair_cap_bucket_reset(&fresh, config, now, ncpus, test_no_refill);
		admitted = fair_cap_bucket_consume(&fresh, config, pkt_len);
		if (bpf_map_update_elem(&service_ingress_cap_state, &key, &fresh,
					BPF_ANY) != 0)
			return -1;
		return admitted;
	}

	if (bucket->cfg_version != config->version)
		fair_cap_bucket_reset(bucket, config, now, ncpus, test_no_refill);
	else if (!test_no_refill)
		fair_cap_bucket_refill(bucket, config, now, ncpus);

	return fair_cap_bucket_consume(bucket, config, pkt_len);
}

static __always_inline int ingress_cap_stage(struct xdp_md *ctx,
						     struct pkt_meta *meta, __u32 slot)
{
	void *data = (void *)(long)ctx->data;
	void *data_end = (void *)(long)ctx->data_end;
	struct fair_config *config;
	__u64 pkt_len = data_end - data;
	int admitted;

	config = fair_config_lookup(slot, meta->service_id);
	if (!config) {
		meta->fair_state = FAIR_ERR;
		return record_drop(meta, DR_MAP_ERROR);
	}

	admitted = fair_cap_admit(config, meta, pkt_len);
	if (admitted < 0) {
		meta->fair_state = FAIR_ERR;
		return record_drop(meta, DR_MAP_ERROR);
	}
	if (!admitted) {
		meta->fair_state = FAIR_CAP_DROP;
		meta->rule_idx = RULE_IDX_NONE;
		write_test_meta(meta);
		return record_drop(meta, DR_INGRESS_CAP_DROP);
	}

	return FAIR_CONTINUE;
}
#endif

#endif
