#ifndef XDP_GATEWAY_SVC_RATELIMIT_H
#define XDP_GATEWAY_SVC_RATELIMIT_H

#include <linux/types.h>

#define SVC_RL_CONFIG_MAX_ENTRIES 1024
#define SVC_RL_STATE_MAX_ENTRIES 1024

enum svc_rl_flags {
	SVC_RL_F_PPS_SET = 1 << 0,
	SVC_RL_F_BPS_SET = 1 << 1,
};

struct svc_rl_config {
	__u32 version;
	__u8 flags;
	__u8 _pad[3];
	__u64 pps;
	__u64 bps;
};

_Static_assert(sizeof(struct svc_rl_config) == 24,
	       "svc_rl_config size is part of the M4 map contract");

#ifdef __BPF__
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>
#include "rules.h"

struct svc_rl_config_inner_map_def {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, SVC_RL_CONFIG_MAX_ENTRIES);
	__type(key, __u32);
	__type(value, struct svc_rl_config);
};

struct svc_rl_config_inner_map_def svc_rl_config_0 SEC(".maps");
struct svc_rl_config_inner_map_def svc_rl_config_1 SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY_OF_MAPS);
	__uint(max_entries, SERVICE_SLOTS);
	__type(key, __u32);
	__array(values, struct svc_rl_config_inner_map_def);
} svc_rl_config_map SEC(".maps") = {
	.values = {
		[0] = &svc_rl_config_0,
		[1] = &svc_rl_config_1,
	},
};

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_HASH);
	__uint(max_entries, SVC_RL_STATE_MAX_ENTRIES);
	__type(key, __u32);
	__type(value, struct rl_bucket);
} svc_rl_state SEC(".maps");

static __always_inline void svc_bucket_reset(struct rl_bucket *bucket,
					     const struct svc_rl_config *config,
					     __u64 now, __u32 ncpus,
					     int test_no_refill)
{
	bucket->cfg_version = config->version;
	bucket->_pad = 0;
	bucket->last_ns = now;
	bucket->pps_tokens = rl_burst(config->pps, ncpus, test_no_refill);
	bucket->bps_tokens = rl_burst(config->bps, ncpus, test_no_refill);
}

static __always_inline void svc_bucket_refill(struct rl_bucket *bucket,
					      const struct svc_rl_config *config,
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

	if (config->flags & SVC_RL_F_PPS_SET) {
		pps_burst = rl_burst(config->pps, ncpus, 0);
		dim_advance = rl_refill_dim(&bucket->pps_tokens, config->pps,
					    pps_burst, elapsed, ncpus);
		if (dim_advance > advance)
			advance = dim_advance;
	}

	if (config->flags & SVC_RL_F_BPS_SET) {
		bps_burst = rl_burst(config->bps, ncpus, 0);
		dim_advance = rl_refill_dim(&bucket->bps_tokens, config->bps,
					    bps_burst, elapsed, ncpus);
		if (dim_advance > advance)
			advance = dim_advance;
	}

	if (advance > 0)
		bucket->last_ns += advance;
}

static __always_inline int svc_bucket_consume(struct rl_bucket *bucket,
					      const struct svc_rl_config *config,
					      __u64 pkt_len)
{
	return rl_bucket_consume_raw(bucket,
				     !!(config->flags & SVC_RL_F_PPS_SET),
				     !!(config->flags & SVC_RL_F_BPS_SET),
				     pkt_len);
}

static __always_inline int svc_rl_admit(__u32 slot, __u32 service_id, __u64 pkt_len)
{
	void *inner = bpf_map_lookup_elem(&svc_rl_config_map, &slot);
	struct svc_rl_config *config;
	struct rl_bucket fresh = {};
	struct rl_bucket *bucket;
	__u32 ncpus;
	__u64 now;
	int test_no_refill;
	int admitted;

	if (!inner)
		return -1;

	config = bpf_map_lookup_elem(inner, &service_id);
	if (!config)
		return 1;

	if (!(config->flags & (SVC_RL_F_PPS_SET | SVC_RL_F_BPS_SET)))
		return 1;

	ncpus = rl_cpu_count();
	test_no_refill = rl_test_no_refill();
	now = bpf_ktime_get_ns();
	bucket = bpf_map_lookup_elem(&svc_rl_state, &service_id);
	if (!bucket) {
		svc_bucket_reset(&fresh, config, now, ncpus, test_no_refill);
		admitted = svc_bucket_consume(&fresh, config, pkt_len);
		if (bpf_map_update_elem(&svc_rl_state, &service_id, &fresh,
					BPF_ANY) != 0)
			return -1;
		return admitted;
	}

	if (bucket->cfg_version != config->version)
		svc_bucket_reset(bucket, config, now, ncpus, test_no_refill);
	else if (!test_no_refill)
		svc_bucket_refill(bucket, config, now, ncpus);

	return svc_bucket_consume(bucket, config, pkt_len);
}
#endif

#endif
