#ifndef XDP_GATEWAY_PARSE_H
#define XDP_GATEWAY_PARSE_H

#include <linux/if_ether.h>
#include <linux/icmp.h>
#include <linux/in.h>
#include <linux/ip.h>
#include <linux/tcp.h>
#include <linux/types.h>
#include <linux/udp.h>

#include <bpf/bpf_endian.h>

#include "pkt_meta.h"

enum parse_result {
	PARSE_OK = 0,
	PARSE_TRUNC,
	PARSE_MALFORMED,
	PARSE_FRAGMENT,
	PARSE_TOO_DEEP,
};

#define IPV4_MF 0x2000
#define IPV4_OFFMASK 0x1fff

struct hdr_cursor {
	void *data;
	void *pos;
	__u16 off;
};

struct vlan_hdr {
	__be16 h_vlan_TCI;
	__be16 h_vlan_encapsulated_proto;
};

static __always_inline int is_vlan_proto(__u16 proto)
{
	return proto == ETH_P_8021Q || proto == ETH_P_8021AD;
}

static __always_inline enum parse_result cursor_advance(struct hdr_cursor *cur,
							void *data_end,
							__u16 len,
							void **hdr)
{
	void *next = cur->pos + len;

	if (next > data_end)
		return PARSE_TRUNC;

	*hdr = cur->pos;
	cur->pos = next;
	cur->off += len;
	return PARSE_OK;
}

static __always_inline enum parse_result parse_vlan(struct hdr_cursor *cur,
						    void *data_end,
						    struct pkt_meta *meta)
{
#pragma unroll
	for (int i = 0; i < 2; i++) {
		struct vlan_hdr *vlan;
		enum parse_result res;

		if (!is_vlan_proto(meta->eth_proto))
			return PARSE_OK;

		res = cursor_advance(cur, data_end, sizeof(*vlan),
				     (void **)&vlan);
		if (res != PARSE_OK)
			return res;

		meta->vlan_depth++;
		meta->eth_proto = bpf_ntohs(vlan->h_vlan_encapsulated_proto);
	}

	if (is_vlan_proto(meta->eth_proto))
		return PARSE_TOO_DEEP;

	return PARSE_OK;
}

static __always_inline enum parse_result parse_eth(struct hdr_cursor *cur,
						   void *data_end,
						   struct pkt_meta *meta)
{
	struct ethhdr *eth;
	enum parse_result res;

	res = cursor_advance(cur, data_end, sizeof(*eth), (void **)&eth);
	if (res != PARSE_OK)
		return res;

	meta->eth_proto = bpf_ntohs(eth->h_proto);
	return parse_vlan(cur, data_end, meta);
}

static __always_inline enum parse_result parse_ipv4(struct hdr_cursor *cur,
						    void *data_end,
						    struct pkt_meta *meta)
{
	struct iphdr *iph;
	enum parse_result res;
	__u16 l3_off = cur->off;
	__u8 version_ihl;
	__u16 ihl_len;
	__u16 total_len;
	__u16 frag_off;
	__u64 available_len;
	void *l4;

	res = cursor_advance(cur, data_end, sizeof(*iph), (void **)&iph);
	if (res != PARSE_OK)
		return PARSE_MALFORMED;

	version_ihl = *(__u8 *)iph;
	if ((version_ihl >> 4) != 4)
		return PARSE_MALFORMED;

	ihl_len = (__u16)(version_ihl & 0x0f) << 2;
	if (ihl_len < sizeof(*iph) || ihl_len > 60)
		return PARSE_MALFORMED;

	l4 = (void *)iph + ihl_len;
	if (l4 > data_end)
		return PARSE_MALFORMED;

	cur->pos = l4;
	cur->off = l3_off + ihl_len;

	total_len = bpf_ntohs(iph->tot_len);
	available_len = data_end - (void *)iph;
	if (total_len < ihl_len || total_len > available_len)
		return PARSE_MALFORMED;

	meta->src_ip = iph->saddr;
	meta->dst_ip = iph->daddr;
	meta->ip_proto = iph->protocol;
	meta->l3_off = l3_off;
	meta->l4_off = l3_off + ihl_len;

	frag_off = bpf_ntohs(iph->frag_off);
	if (frag_off & (IPV4_MF | IPV4_OFFMASK)) {
		meta->is_fragment = 1;
		return PARSE_FRAGMENT;
	}

	return PARSE_OK;
}

static __always_inline enum parse_result parse_l4(struct hdr_cursor *cur,
						  void *data_end,
						  struct pkt_meta *meta)
{
	enum parse_result res;

	switch (meta->ip_proto) {
	case IPPROTO_TCP: {
		struct tcphdr *tcp;

		res = cursor_advance(cur, data_end, sizeof(*tcp),
				     (void **)&tcp);
		if (res != PARSE_OK)
			return PARSE_MALFORMED;

		meta->sport = tcp->source;
		meta->dport = tcp->dest;
		return PARSE_OK;
	}
	case IPPROTO_UDP: {
		struct udphdr *udp;

		res = cursor_advance(cur, data_end, sizeof(*udp),
				     (void **)&udp);
		if (res != PARSE_OK)
			return PARSE_MALFORMED;

		meta->sport = udp->source;
		meta->dport = udp->dest;
		return PARSE_OK;
	}
	case IPPROTO_ICMP: {
		struct icmphdr *icmp;

		res = cursor_advance(cur, data_end, sizeof(*icmp),
				     (void **)&icmp);
		if (res != PARSE_OK)
			return PARSE_MALFORMED;

		meta->icmp_type = icmp->type;
		meta->icmp_code = icmp->code;
		return PARSE_OK;
	}
	default:
		return PARSE_OK;
	}
}

#endif
