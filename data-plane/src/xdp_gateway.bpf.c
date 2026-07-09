#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <bpf/bpf_helpers.h>

#include "drop_reason.h"
#include "parse.h"
#include "pkt_meta.h"
#include "service.h"

static __always_inline int redirect_out(struct pkt_meta *meta);
static __always_inline void write_test_meta(const struct pkt_meta *meta);

#include "rules.h"
#include "whitelist.h"

struct service_inner_map_def {
	__uint(type, BPF_MAP_TYPE_LPM_TRIE);
	__uint(max_entries, 1024);
	__uint(map_flags, BPF_F_NO_PREALLOC);
	__type(key, struct service_key);
	__type(value, struct service_val);
};

struct service_inner_map_def service_inner_0 SEC(".maps");
struct service_inner_map_def service_inner_1 SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY_OF_MAPS);
	__uint(max_entries, SERVICE_SLOTS);
	__type(key, __u32);
	__array(values, struct service_inner_map_def);
} service_map SEC(".maps") = {
	.values = {
		[0] = &service_inner_0,
		[1] = &service_inner_1,
	},
};

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, 1);
	__type(key, __u32);
	__type(value, struct active_config);
} active_config SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_DEVMAP);
	__uint(max_entries, 1);
	__type(key, __u32);
	__type(value, __u32);
} tx_devmap SEC(".maps");

#ifdef PKT_TEST_HOOKS
struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, 1);
	__type(key, __u32);
	__type(value, struct pkt_meta);
} test_meta_map SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, 1);
	__type(key, __u32);
	__type(value, __u32);
} test_trigger_map SEC(".maps");
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

static __always_inline int test_bad_reason_enabled(void)
{
#ifdef PKT_TEST_HOOKS
	__u32 key = 0;
	__u32 *trigger = bpf_map_lookup_elem(&test_trigger_map, &key);

	return trigger && *trigger == 1;
#else
	return 0;
#endif
}

static __always_inline int test_whitelist_bloom_probe(struct pkt_meta *meta)
{
#ifdef PKT_TEST_HOOKS
	__u32 key = 0;
	__u32 *trigger = bpf_map_lookup_elem(&test_trigger_map, &key);

	if (!trigger || (*trigger != 2 && *trigger != 3))
		return -1;

	if (whitelist_test_bloom_probe(0, *trigger == 2) != 0)
		return record_drop(meta, DR_MAP_ERROR);
	return XDP_PASS;
#else
	(void)meta;
	return -1;
#endif
}

static __always_inline int redirect_out(struct pkt_meta *meta)
{
	meta->verdict = PKT_VERDICT_REDIRECT;
	write_test_meta(meta);
	return bpf_redirect_map(&tx_devmap, 0, XDP_DROP);
}

static __always_inline int service_lookup_redirect(struct xdp_md *ctx,
						   struct pkt_meta *meta)
{
	__u32 config_key = 0;
	struct active_config *config;
	struct service_key key = {};
	struct service_val *service;
	void *inner;
	__u32 slot;

	config = bpf_map_lookup_elem(&active_config, &config_key);
	if (!config)
		return record_drop(meta, DR_MAP_ERROR);

	slot = config->active_slot;
	meta->active_slot = (__u8)slot;

	inner = bpf_map_lookup_elem(&service_map, &slot);
	if (!inner)
		return record_drop(meta, DR_MAP_ERROR);

	key.prefixlen = 32;
	key.addr = meta->dst_ip;
	service = bpf_map_lookup_elem(inner, &key);
	if (!service)
		return record_drop(meta, DR_SERVICE_MISS);

	if (!service->enabled)
		return record_drop(meta, DR_SERVICE_DISABLED);

	meta->service_id = service->service_id;
	/* WLV-24 seam A: M3#4 ingress-cost cap inserts here. */
	return whitelist_stage(ctx, meta, slot, service->wl_flags);
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
	int test_ret;

	if (test_bad_reason_enabled())
		return record_drop(&meta, (enum drop_reason)DROP_REASON_CAP);
	test_ret = test_whitelist_bloom_probe(&meta);
	if (test_ret >= 0)
		return test_ret;

	res = parse_eth(&cur, data_end, &meta);
	if (res != PARSE_OK)
		return record_drop(&meta, DR_UNSUPPORTED_ETHERTYPE);

	switch (meta.eth_proto) {
	case ETH_P_IPV6:
		return record_drop(&meta, DR_IPV6_UNSUPPORTED);
	case ETH_P_ARP:
		return redirect_out(&meta);
	case ETH_P_IP:
		res = parse_ipv4(&cur, data_end, &meta);
		if (res == PARSE_FRAGMENT)
			return record_drop(&meta, DR_FRAGMENT_UNSUPPORTED);
		if (res != PARSE_OK)
			return record_drop(&meta, DR_MALFORMED_IPV4);

		res = parse_l4(&cur, data_end, &meta);
		if (res != PARSE_OK)
			return record_drop(&meta, DR_MALFORMED_IPV4);

		return service_lookup_redirect(ctx, &meta);
	default:
		return record_drop(&meta, DR_UNSUPPORTED_ETHERTYPE);
	}

	return XDP_PASS;
}

char _license[] SEC("license") = "GPL";
