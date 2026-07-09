#ifndef XDP_GATEWAY_PKT_META_H
#define XDP_GATEWAY_PKT_META_H

#include <linux/types.h>

struct pkt_meta {
	__u32 src_ip;
	__u32 dst_ip;
	__u32 service_id;
	__u16 eth_proto;
	__u16 sport;
	__u16 dport;
	__u16 l3_off;
	__u16 l4_off;
	__u8 ip_proto;
	__u8 vlan_depth;
	__u8 icmp_type;
	__u8 icmp_code;
	__u8 is_fragment;
	__u8 active_slot;
	__u8 verdict;
	__u8 rule_idx;
	__u8 _pad[2];
};

_Static_assert(sizeof(struct pkt_meta) == 32,
	       "pkt_meta size is part of the test hook ABI");

#endif
