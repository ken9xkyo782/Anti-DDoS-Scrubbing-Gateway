#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <bpf/bpf_helpers.h>

#include "drop_reason.h"
#include "parse.h"
#include "pkt_meta.h"
#include "service.h"

#ifdef PKT_TEST_HOOKS
struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, 1);
	__type(key, __u32);
	__type(value, struct pkt_meta);
} test_meta_map SEC(".maps");
#endif

static __always_inline void write_test_meta(const struct pkt_meta *meta)
{
#ifdef PKT_TEST_HOOKS
	__u32 key = 0;

	bpf_map_update_elem(&test_meta_map, &key, meta, BPF_ANY);
#else
	(void)meta;
#endif
}

SEC("xdp")
int xdp_gateway(struct xdp_md *ctx)
{
	void *data = (void *)(long)ctx->data;
	void *data_end = (void *)(long)ctx->data_end;
	struct hdr_cursor cur = {
		.data = data,
		.pos = data,
		.off = 0,
	};
	struct pkt_meta meta = {};
	enum parse_result res;

	res = parse_eth(&cur, data_end, &meta);
	if (res != PARSE_OK)
		return record_drop(DR_UNSUPPORTED_ETHERTYPE);

	switch (meta.eth_proto) {
	case ETH_P_IPV6:
		return record_drop(DR_IPV6_UNSUPPORTED);
	case ETH_P_ARP:
		/* SEAM: redirect ARP in service lookup feature */
		return XDP_PASS;
	case ETH_P_IP:
		res = parse_ipv4(&cur, data_end, &meta);
		if (res == PARSE_FRAGMENT)
			return record_drop(DR_FRAGMENT_UNSUPPORTED);
		if (res != PARSE_OK)
			return record_drop(DR_MALFORMED_IPV4);

		res = parse_l4(&cur, data_end, &meta);
		if (res != PARSE_OK)
			return record_drop(DR_MALFORMED_IPV4);

		write_test_meta(&meta);

		/* SEAM: service lookup (next feature) */
		return XDP_PASS;
	default:
		return record_drop(DR_UNSUPPORTED_ETHERTYPE);
	}

	return XDP_PASS;
}

char _license[] SEC("license") = "GPL";
