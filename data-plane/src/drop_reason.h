#ifndef XDP_GATEWAY_DROP_REASON_H
#define XDP_GATEWAY_DROP_REASON_H

#include <linux/types.h>

/* FROZEN ABI: append only; never renumber existing reasons. */
enum drop_reason {
	DR_IPV6_UNSUPPORTED = 0,
	DR_UNSUPPORTED_ETHERTYPE = 1,
	DR_MALFORMED_IPV4 = 2,
	DR_FRAGMENT_UNSUPPORTED = 3,
	DR_BOGON_DROP = 4,
	DR_SERVICE_MISS = 5,
	DR_SERVICE_DISABLED = 6,
	DR_UDP_AMPLIFICATION_DROP = 7,
	DR_BLACKLIST_DROP = 8,
	DR_NOT_ALLOWED = 9,
	DR_RATE_LIMIT_DROP = 10,
	DR_SERVICE_CEILING_DROP = 11,
	DR_CONGESTION_DROP = 12,
	DR_INGRESS_CAP_DROP = 13,
	DR_VIP_CEILING_DROP = 14,
	DR_MAP_ERROR = 15,
	DROP_REASON_COUNT = 16,
	DROP_REASON_CAP = 32,
};

_Static_assert(DROP_REASON_COUNT <= DROP_REASON_CAP,
	       "drop reason ABI exceeds counter map capacity");

#include "svc_stat.h"

#ifndef __BPF__
static const char *const drop_reason_name[DROP_REASON_COUNT] = {
	[DR_IPV6_UNSUPPORTED] = "ipv6_unsupported",
	[DR_UNSUPPORTED_ETHERTYPE] = "unsupported_ethertype",
	[DR_MALFORMED_IPV4] = "malformed_ipv4",
	[DR_FRAGMENT_UNSUPPORTED] = "fragment_unsupported",
	[DR_BOGON_DROP] = "bogon_drop",
	[DR_SERVICE_MISS] = "service_miss",
	[DR_SERVICE_DISABLED] = "service_disabled",
	[DR_UDP_AMPLIFICATION_DROP] = "udp_amplification_drop",
	[DR_BLACKLIST_DROP] = "blacklist_drop",
	[DR_NOT_ALLOWED] = "not_allowed",
	[DR_RATE_LIMIT_DROP] = "rate_limit_drop",
	[DR_SERVICE_CEILING_DROP] = "service_ceiling_drop",
	[DR_CONGESTION_DROP] = "congestion_drop",
	[DR_INGRESS_CAP_DROP] = "ingress_cap_drop",
	[DR_VIP_CEILING_DROP] = "vip_ceiling_drop",
	[DR_MAP_ERROR] = "map_error",
};
#endif

#ifdef __BPF__
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

#include "pkt_meta.h"
#include "sample.h"

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
	__uint(max_entries, DROP_REASON_CAP);
	__type(key, __u32);
	__type(value, __u64);
} counter_map SEC(".maps");

static __always_inline int record_drop(const struct pkt_meta *meta,
				       enum drop_reason reason)
{
	__u32 key = (__u32)reason;
	__u64 *count = bpf_map_lookup_elem(&counter_map, &key);

	if ((__u32)reason >= DROP_REASON_COUNT) {
		reason = DR_MAP_ERROR;
		key = (__u32)reason;
		count = bpf_map_lookup_elem(&counter_map, &key);
	}

	if (count)
		__sync_fetch_and_add(count, 1);
	svc_stat_drop(meta, key);
	sample_drop(meta, key);

	return XDP_DROP;
}
#endif

#endif
