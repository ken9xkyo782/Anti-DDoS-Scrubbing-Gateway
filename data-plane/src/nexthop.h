#ifndef XDP_GATEWAY_NEXTHOP_H
#define XDP_GATEWAY_NEXTHOP_H

#include <linux/types.h>

struct nexthop {
	__u8 dst_mac[6];
	__u8 src_mac[6];
	__u8 resolved;
	__u8 _pad;
	__u64 last_resolved_ns;
};

_Static_assert(sizeof(struct nexthop) == 24, "sizeof(struct nexthop) must be 24");

#ifdef __BPF__
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>
#include <linux/if_ether.h>

#include "pkt_meta.h"

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 1024);
	__type(key, __u32);
	__type(value, struct nexthop);
	__uint(map_flags, BPF_F_NO_PREALLOC);
} nexthop_map SEC(".maps");

static __always_inline int nexthop_rewrite(struct xdp_md *ctx,
					   struct pkt_meta *meta)
{
	void *data = (void *)(long)ctx->data;
	void *data_end = (void *)(long)ctx->data_end;
	struct ethhdr *eth = data;
	struct nexthop *nh;
	__u32 key = meta->service_id;

	if (meta->eth_proto != ETH_P_IP)
		return 0;

	if ((void *)(eth + 1) > data_end)
		return -1;

	nh = bpf_map_lookup_elem(&nexthop_map, &key);
	if (!nh || !nh->resolved)
		return -1;

	__builtin_memcpy(eth->h_dest, nh->dst_mac, ETH_ALEN);
	__builtin_memcpy(eth->h_source, nh->src_mac, ETH_ALEN);
	return 0;
}
#endif

#endif
