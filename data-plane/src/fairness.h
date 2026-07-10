#ifndef XDP_GATEWAY_FAIRNESS_H
#define XDP_GATEWAY_FAIRNESS_H

#include <linux/bpf.h>
#include <linux/types.h>

#include "pkt_meta.h"
#include "rules.h"

#define FAIR_CONFIG_MAX_ENTRIES 1024
#define FAIR_RATE_MAX 16000000000ULL

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
#endif

#endif
