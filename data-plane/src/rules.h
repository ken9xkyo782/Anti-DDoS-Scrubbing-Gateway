#ifndef XDP_GATEWAY_RULES_H
#define XDP_GATEWAY_RULES_H

#include <linux/types.h>

#include "pkt_meta.h"
#include "service.h"

#define RULE_MAX 16
#define RULE_PROTO_ANY 0
#define RULE_IDX_NONE 0xff
#define RULE_BLOCK_MAX_ENTRIES 1024
#define RATE_LIMIT_STATE_MAX_ENTRIES (RULE_BLOCK_MAX_ENTRIES * RULE_MAX)

enum rule_flags {
	RULE_F_ENABLED = 1 << 0,
	RULE_F_PPS_SET = 1 << 1,
	RULE_F_BPS_SET = 1 << 2,
};

struct rule_entry {
	__u16 src_lo;
	__u16 src_hi;
	__u16 dst_lo;
	__u16 dst_hi;
	__u64 pps;
	__u64 bps; /* bytes/sec; M4 converts from the control-plane unit. */
	__u8 proto;
	__u8 flags;
	__u8 _pad[6];
};

struct rule_block {
	__u32 version;
	__u16 rule_count;
	__u16 _pad;
	/* Pre-sorted ascending priority; position is the hot-path match order. */
	struct rule_entry rules[RULE_MAX];
};

struct rl_key {
	__u32 service_id;
	__u32 rule_idx;
};

struct rl_bucket {
	__u32 cfg_version;
	__u32 _pad;
	__u64 last_ns;
	__u64 pps_tokens;
	__u64 bps_tokens;
};

struct rl_config {
	__u32 test_no_refill;
	__u32 _pad;
};

_Static_assert(sizeof(struct rule_entry) == 32,
	       "rule_entry size is part of the M4 map contract");
_Static_assert(sizeof(struct rule_block) == 520,
	       "rule_block size is part of the M4 map contract");
_Static_assert(sizeof(struct rl_bucket) == 32,
	       "rl_bucket size is part of the runtime map contract");

#ifdef __BPF__
#include <linux/bpf.h>
#include <linux/in.h>
#include <bpf/bpf_endian.h>
#include <bpf/bpf_helpers.h>

#include "drop_reason.h"

const volatile __u32 rl_ncpus = 1;

struct rule_block_inner_map_def {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, RULE_BLOCK_MAX_ENTRIES);
	__type(key, __u32);
	__type(value, struct rule_block);
};

struct rule_block_inner_map_def rule_block_0 SEC(".maps");
struct rule_block_inner_map_def rule_block_1 SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY_OF_MAPS);
	__uint(max_entries, SERVICE_SLOTS);
	__type(key, __u32);
	__array(values, struct rule_block_inner_map_def);
} rule_block_map SEC(".maps") = {
	.values = {
		[0] = &rule_block_0,
		[1] = &rule_block_1,
	},
};

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_HASH);
	__uint(max_entries, RATE_LIMIT_STATE_MAX_ENTRIES);
	__type(key, struct rl_key);
	__type(value, struct rl_bucket);
} rate_limit_state SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, 1);
	__type(key, __u32);
	__type(value, struct rl_config);
} rl_config SEC(".maps");

static __always_inline int rule_proto_matches(const struct rule_entry *rule,
					      const struct pkt_meta *meta)
{
	if (rule->proto == RULE_PROTO_ANY)
		return meta->ip_proto == IPPROTO_TCP ||
		       meta->ip_proto == IPPROTO_UDP ||
		       meta->ip_proto == IPPROTO_ICMP;

	return meta->ip_proto == rule->proto;
}

static __always_inline int port_in_range(__u16 port, __u16 lo, __u16 hi)
{
	return port >= lo && port <= hi;
}

static __always_inline int rule_ports_match(const struct rule_entry *rule,
					    const struct pkt_meta *meta)
{
	__u16 sport;
	__u16 dport;

	if (rule->proto != IPPROTO_TCP && rule->proto != IPPROTO_UDP)
		return 1;

	sport = bpf_ntohs(meta->sport);
	dport = bpf_ntohs(meta->dport);
	return port_in_range(sport, rule->src_lo, rule->src_hi) &&
	       port_in_range(dport, rule->dst_lo, rule->dst_hi);
}

static __always_inline int rule_matches(const struct rule_entry *rule,
					const struct pkt_meta *meta)
{
	if (!(rule->flags & RULE_F_ENABLED))
		return 0;
	if (!rule_proto_matches(rule, meta))
		return 0;
	return rule_ports_match(rule, meta);
}

static __always_inline __u32 rl_cpu_count(void)
{
	return rl_ncpus ? rl_ncpus : 1;
}

static __always_inline int rl_test_no_refill(void)
{
	__u32 key = 0;
	struct rl_config *config = bpf_map_lookup_elem(&rl_config, &key);

	return config && config->test_no_refill;
}

static __always_inline __u64 rl_burst(__u64 rate, __u32 ncpus,
				      int test_no_refill)
{
	__u64 burst;

	if (rate == 0)
		return 0;
	if (test_no_refill)
		return rate;

	burst = rate / ncpus;
	return burst ? burst : 1;
}

static __always_inline void rl_bucket_reset(struct rl_bucket *bucket,
					    const struct rule_block *block,
					    const struct rule_entry *rule,
					    __u64 now, __u32 ncpus,
					    int test_no_refill)
{
	bucket->cfg_version = block->version;
	bucket->_pad = 0;
	bucket->last_ns = now;
	bucket->pps_tokens = rl_burst(rule->pps, ncpus, test_no_refill);
	bucket->bps_tokens = rl_burst(rule->bps, ncpus, test_no_refill);
}

static __always_inline __u64 rl_refill_dim(__u64 *tokens, __u64 rate,
					   __u64 burst, __u64 elapsed,
					   __u32 ncpus)
{
	__u64 denom = NSEC_PER_SEC * (__u64)ncpus;
	__u64 grant;
	__u64 space;
	__u64 advance;

	if (rate == 0 || *tokens >= burst)
		return 0;

	grant = elapsed * rate / denom;
	if (grant == 0)
		return 0;

	space = burst - *tokens;
	if (grant > space)
		grant = space;

	*tokens += grant;
	advance = grant * denom / rate;
	if (advance == 0)
		advance = 1;
	return advance > elapsed ? elapsed : advance;
}

static __always_inline void rl_bucket_refill(struct rl_bucket *bucket,
					     const struct rule_entry *rule,
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

	if (rule->flags & RULE_F_PPS_SET) {
		pps_burst = rl_burst(rule->pps, ncpus, 0);
		dim_advance = rl_refill_dim(&bucket->pps_tokens, rule->pps,
					    pps_burst, elapsed, ncpus);
		if (dim_advance > advance)
			advance = dim_advance;
	}

	if (rule->flags & RULE_F_BPS_SET) {
		bps_burst = rl_burst(rule->bps, ncpus, 0);
		dim_advance = rl_refill_dim(&bucket->bps_tokens, rule->bps,
					    bps_burst, elapsed, ncpus);
		if (dim_advance > advance)
			advance = dim_advance;
	}

	if (advance > 0)
		bucket->last_ns += advance;
}

static __always_inline int rl_bucket_consume(struct rl_bucket *bucket,
					     const struct rule_entry *rule,
					     __u64 pkt_len)
{
	int pps_set = rule->flags & RULE_F_PPS_SET;
	int bps_set = rule->flags & RULE_F_BPS_SET;
	int pps_ok = !pps_set || bucket->pps_tokens >= 1;
	int bps_ok = !bps_set || bucket->bps_tokens >= pkt_len;

	if (!pps_ok || !bps_ok)
		return 0;

	if (pps_set)
		bucket->pps_tokens--;
	if (bps_set)
		bucket->bps_tokens -= pkt_len;
	return 1;
}

static __always_inline int rl_bucket_admit(const struct rule_block *block,
					   const struct rule_entry *rule,
					   const struct pkt_meta *meta,
					   __u32 rule_idx, __u64 pkt_len)
{
	struct rl_key key = {
		.service_id = meta->service_id,
		.rule_idx = rule_idx,
	};
	struct rl_bucket fresh = {};
	struct rl_bucket *bucket;
	__u32 ncpus;
	__u64 now;
	int test_no_refill;
	int admitted;

	if (!(rule->flags & (RULE_F_PPS_SET | RULE_F_BPS_SET)))
		return 1;

	ncpus = rl_cpu_count();
	test_no_refill = rl_test_no_refill();
	now = bpf_ktime_get_ns();
	bucket = bpf_map_lookup_elem(&rate_limit_state, &key);
	if (!bucket) {
		rl_bucket_reset(&fresh, block, rule, now, ncpus, test_no_refill);
		admitted = rl_bucket_consume(&fresh, rule, pkt_len);
		if (bpf_map_update_elem(&rate_limit_state, &key, &fresh,
					BPF_ANY) != 0)
			return -1;
		return admitted;
	}

	if (bucket->cfg_version != block->version)
		rl_bucket_reset(bucket, block, rule, now, ncpus, test_no_refill);
	else if (!test_no_refill)
		rl_bucket_refill(bucket, rule, now, ncpus);

	return rl_bucket_consume(bucket, rule, pkt_len);
}

static __always_inline int fair_admit_stage(struct xdp_md *ctx,
					    struct pkt_meta *meta, __u32 slot);

static __always_inline int admit_clean(struct xdp_md *ctx,
					   struct pkt_meta *meta, __u32 slot)
{
	return fair_admit_stage(ctx, meta, slot);
}

static __always_inline int allow_rule_stage(struct xdp_md *ctx,
					    struct pkt_meta *meta, __u32 slot)
{
	struct rule_block *block;
	__u32 service_id = meta->service_id;
	void *data = (void *)(long)ctx->data;
	void *data_end = (void *)(long)ctx->data_end;
	__u64 pkt_len = data_end - data;
	__u32 count;
	void *inner;
	int admitted;
	meta->rule_idx = RULE_IDX_NONE;

	inner = bpf_map_lookup_elem(&rule_block_map, &slot);
	if (!inner)
		return record_drop(meta, DR_MAP_ERROR);

	block = bpf_map_lookup_elem(inner, &service_id);
	if (!block)
		return record_drop(meta, DR_NOT_ALLOWED);

	count = block->rule_count;
	if (count > RULE_MAX)
		count = RULE_MAX;

#pragma clang loop unroll(disable)
	for (__u32 i = 0; i < RULE_MAX; i++) {
		if (i >= count)
			break;
		if (!rule_matches(&block->rules[i], meta))
			continue;

		meta->rule_idx = (__u8)i;
		admitted = rl_bucket_admit(block, &block->rules[i], meta, i,
					   pkt_len);
		if (admitted < 0)
			return record_drop(meta, DR_MAP_ERROR);
		if (!admitted)
			return record_drop(meta, DR_RATE_LIMIT_DROP);
		return admit_clean(ctx, meta, slot);
	}

	return record_drop(meta, DR_NOT_ALLOWED);
}
#endif

#endif
