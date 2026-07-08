#ifndef XDP_GATEWAY_PARSE_H
#define XDP_GATEWAY_PARSE_H

#include <linux/if_ether.h>
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

#endif
