#ifndef XDP_GATEWAY_DROP_REASON_H
#define XDP_GATEWAY_DROP_REASON_H

#include <linux/types.h>

enum drop_reason {
	DR_IPV6_UNSUPPORTED = 0,
	DR_UNSUPPORTED_ETHERTYPE,
	DR_MALFORMED_IPV4,
	DR_FRAGMENT_UNSUPPORTED,
	DR_MAP_ERROR,
	DR_SERVICE_MISS,
	DR_SERVICE_DISABLED,
	DROP_REASON_CAP = 32,
};

#ifdef __BPF__
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
	__uint(max_entries, DROP_REASON_CAP);
	__type(key, __u32);
	__type(value, __u64);
} counter_map SEC(".maps");

static __always_inline int record_drop(enum drop_reason reason)
{
	__u32 key = (__u32)reason;
	__u64 *count = bpf_map_lookup_elem(&counter_map, &key);

	if (count)
		__sync_fetch_and_add(count, 1);

	return XDP_DROP;
}
#endif

#endif
