#ifndef XDP_GATEWAY_FAIR_BUDGET_H
#define XDP_GATEWAY_FAIR_BUDGET_H

#include <linux/types.h>

#include "fairness.h"

struct fair_budget {
	__u64 committed_bps;
	__u64 burst_bps;
	__u64 cap_bps;
	__u64 cap_pps;
};

static inline __u64 clamp_fair_rate(__u64 value)
{
	return value > FAIR_RATE_MAX ? FAIR_RATE_MAX : value;
}

static inline __u64 fair_rate_product(__u64 left, __u64 right)
{
	if (left == 0 || right == 0)
		return 0;
	if (left > FAIR_RATE_MAX / right)
		return FAIR_RATE_MAX;
	return left * right;
}

/* ref_pkt must be non-zero; callers validate configuration before calling. */
static inline struct fair_budget fair_budget(__u64 committed_bps,
						     __u64 ceiling_bps, __u64 k,
						     __u64 ref_pkt)
{
	struct fair_budget budget;

	budget.committed_bps = clamp_fair_rate(committed_bps);
	ceiling_bps = clamp_fair_rate(ceiling_bps);
	budget.burst_bps = ceiling_bps - budget.committed_bps;
	budget.cap_bps = fair_rate_product(ceiling_bps, k);
	budget.cap_pps = budget.cap_bps / ref_pkt;
	return budget;
}

static inline __u64 node_headroom(__u64 capacity, __u64 sum_committed)
{
	return capacity > sum_committed ? capacity - sum_committed : 0;
}

#endif
