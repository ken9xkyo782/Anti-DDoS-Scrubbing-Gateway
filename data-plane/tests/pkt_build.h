#ifndef XDP_GATEWAY_PKT_BUILD_H
#define XDP_GATEWAY_PKT_BUILD_H

#include <arpa/inet.h>
#include <linux/icmp.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/tcp.h>
#include <linux/udp.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define PKT_FRAME_CAP 256
#define IPV4_MF 0x2000
#define IPV4_OFFMASK 0x1fff

struct pkt_frame {
	uint8_t data[PKT_FRAME_CAP];
	size_t len;
	size_t ipv4_off;
	bool has_ipv4;
};

struct pkt_vlan_hdr {
	uint16_t h_vlan_TCI;
	uint16_t h_vlan_encapsulated_proto;
} __attribute__((packed));

static inline void pkt_frame_init(struct pkt_frame *frame)
{
	memset(frame, 0, sizeof(*frame));
}

static inline void *pkt_append(struct pkt_frame *frame, size_t len)
{
	void *pos;

	if (frame->len + len > sizeof(frame->data))
		return NULL;

	pos = frame->data + frame->len;
	frame->len += len;
	return pos;
}

static inline int pkt_fix_ipv4_tot_len(struct pkt_frame *frame)
{
	struct iphdr *iph;

	if (!frame->has_ipv4)
		return 0;

	iph = (struct iphdr *)(frame->data + frame->ipv4_off);
	iph->tot_len = htons(frame->len - frame->ipv4_off);
	return 0;
}

static inline int build_eth(struct pkt_frame *frame, uint16_t ethertype)
{
	struct ethhdr *eth = pkt_append(frame, sizeof(*eth));

	if (!eth)
		return -1;

	memset(eth->h_dest, 0xaa, sizeof(eth->h_dest));
	memset(eth->h_source, 0xbb, sizeof(eth->h_source));
	eth->h_proto = htons(ethertype);
	return 0;
}

static inline int append_vlan_tag(struct pkt_frame *frame, uint16_t inner_ethertype)
{
	struct pkt_vlan_hdr *vlan = pkt_append(frame, sizeof(*vlan));

	if (!vlan)
		return -1;

	vlan->h_vlan_TCI = htons(1);
	vlan->h_vlan_encapsulated_proto = htons(inner_ethertype);
	return 0;
}

static inline int build_vlan(struct pkt_frame *frame, uint16_t inner_ethertype)
{
	return build_eth(frame, ETH_P_8021Q) ||
	       append_vlan_tag(frame, inner_ethertype);
}

static inline int build_qinq(struct pkt_frame *frame, uint16_t inner_ethertype)
{
	return build_eth(frame, ETH_P_8021AD) ||
	       append_vlan_tag(frame, ETH_P_8021Q) ||
	       append_vlan_tag(frame, inner_ethertype);
}

static inline int build_ipv4(struct pkt_frame *frame, uint8_t proto,
			     uint16_t frag_off, uint8_t ihl)
{
	size_t hdr_len = (size_t)ihl * 4;
	struct iphdr *iph;

	if (hdr_len < sizeof(*iph))
		hdr_len = sizeof(*iph);

	iph = pkt_append(frame, hdr_len);
	if (!iph)
		return -1;

	memset(iph, 0, hdr_len);
	iph->version = 4;
	iph->ihl = ihl;
	iph->ttl = 64;
	iph->protocol = proto;
	iph->frag_off = htons(frag_off);
	iph->saddr = htonl(0x0a000001);
	iph->daddr = htonl(0x0a000002);

	frame->ipv4_off = (uint8_t *)iph - frame->data;
	frame->has_ipv4 = true;
	return pkt_fix_ipv4_tot_len(frame);
}

static inline int build_tcp(struct pkt_frame *frame, uint16_t sport,
			    uint16_t dport)
{
	struct tcphdr *tcp = pkt_append(frame, sizeof(*tcp));

	if (!tcp)
		return -1;

	memset(tcp, 0, sizeof(*tcp));
	tcp->source = htons(sport);
	tcp->dest = htons(dport);
	tcp->doff = 5;
	return pkt_fix_ipv4_tot_len(frame);
}

static inline int build_udp(struct pkt_frame *frame, uint16_t sport,
			    uint16_t dport)
{
	struct udphdr *udp = pkt_append(frame, sizeof(*udp));

	if (!udp)
		return -1;

	memset(udp, 0, sizeof(*udp));
	udp->source = htons(sport);
	udp->dest = htons(dport);
	udp->len = htons(sizeof(*udp));
	return pkt_fix_ipv4_tot_len(frame);
}

static inline int build_icmp(struct pkt_frame *frame, uint8_t type,
			     uint8_t code)
{
	struct icmphdr *icmp = pkt_append(frame, sizeof(*icmp));

	if (!icmp)
		return -1;

	memset(icmp, 0, sizeof(*icmp));
	icmp->type = type;
	icmp->code = code;
	return pkt_fix_ipv4_tot_len(frame);
}

static inline int build_arp(struct pkt_frame *frame)
{
	uint8_t *arp = pkt_append(frame, 28);

	if (!arp)
		return -1;

	memset(arp, 0, 28);
	return 0;
}

static inline int build_ipv6(struct pkt_frame *frame)
{
	uint8_t *ipv6 = pkt_append(frame, 40);

	if (!ipv6)
		return -1;

	memset(ipv6, 0, 40);
	ipv6[0] = 0x60;
	return 0;
}

#endif
