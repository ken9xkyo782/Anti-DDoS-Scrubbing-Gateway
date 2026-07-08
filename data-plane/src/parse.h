#ifndef XDP_GATEWAY_PARSE_H
#define XDP_GATEWAY_PARSE_H

#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/types.h>

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
	return PARSE_OK;
}

static __always_inline enum parse_result parse_ipv4(struct hdr_cursor *cur,
						    void *data_end,
						    struct pkt_meta *meta)
{
	struct iphdr *iph;
	enum parse_result res;
	__u16 l3_off = cur->off;
	__u8 version_ihl;
	__u8 ihl;
	__u16 ihl_len;
	__u16 total_len;
	__u16 frag_off;
	__u64 available_len;

	res = cursor_advance(cur, data_end, sizeof(*iph), (void **)&iph);
	if (res != PARSE_OK)
		return PARSE_MALFORMED;

	version_ihl = *(__u8 *)iph;
	if ((version_ihl >> 4) != 4)
		return PARSE_MALFORMED;

	ihl = version_ihl & 0x0f;
	if (ihl < 5)
		return PARSE_MALFORMED;

	ihl_len = (__u16)ihl * 4;
	if (ihl_len > sizeof(*iph)) {
		__u16 options_len = ihl_len - sizeof(*iph);
		void *options_end = cur->pos + options_len;

		if (options_end > data_end)
			return PARSE_MALFORMED;

		cur->pos = options_end;
		cur->off += options_len;
	}

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

#endif
