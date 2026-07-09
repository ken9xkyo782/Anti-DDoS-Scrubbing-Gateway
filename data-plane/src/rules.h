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

static __always_inline int admit_clean(struct pkt_meta *meta)
{
	/* ARL-24: M3 fairness ladder inserts here before redirect_out(). */
	return redirect_out(meta);
}

static __always_inline int allow_rule_stage(struct xdp_md *ctx,
					    struct pkt_meta *meta, __u32 slot)
{
	struct rule_block *block;
	__u32 service_id = meta->service_id;
	__u32 count;
	void *inner;

	(void)ctx;
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
		return admit_clean(meta);
	}

	return record_drop(meta, DR_NOT_ALLOWED);
}
#endif

#endif
