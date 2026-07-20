#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <bpf/bpf_helpers.h>

#include "drop_reason.h"
#include "parse.h"
#include "pkt_meta.h"
#include "service.h"

#ifndef AF_INET
#define AF_INET 2
#endif

static __always_inline void l3_rewrite_nexthop(struct xdp_md *ctx,
					       struct pkt_meta *meta);
static __always_inline int redirect_out(struct xdp_md *ctx,
					struct pkt_meta *meta);
static __always_inline void write_test_meta(const struct pkt_meta *meta);

#include "rules.h"
#include "blacklist.h"
#include "whitelist.h"
#include "fairness.h"

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

#include "node_control.h"

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

static __always_inline int test_fair_spin_lock_probe(struct pkt_meta *meta)
{
#ifdef PKT_TEST_HOOKS
	__u32 key = 0;
	__u32 *trigger = bpf_map_lookup_elem(&test_trigger_map, &key);

	if (!trigger || *trigger != FAIR_TEST_TRIGGER_SPIN_LOCK)
		return -1;
	if (fair_test_spin_lock_mutate() != 0)
		return record_drop(meta, DR_MAP_ERROR);
	return XDP_PASS;
#else
	(void)meta;
	return -1;
#endif
}

/*
 * L3 next-hop rewrite for routed (non-transparent-bridge) deployments. When the
 * gateway has IPs on IN/OUT and forwards by route, ingress frames to a service
 * carry the gateway's own IN MAC as destination, so a verbatim redirect reaches
 * the backend with the wrong dst MAC and is dropped at L2. bpf_fib_lookup
 * resolves the egress next-hop; on success we rewrite the outer Ethernet dst/src
 * MAC. On any non-SUCCESS result (ARP, pure L2 transparent bridge, unresolved
 * neighbor, or veth smoke tests with no route) the frame is left untouched, so
 * the original transparent-bridge behavior is preserved as a fallback.
 */
static __always_inline void l3_rewrite_nexthop(struct xdp_md *ctx,
					       struct pkt_meta *meta)
{
	void *data = (void *)(long)ctx->data;
	void *data_end = (void *)(long)ctx->data_end;
	struct ethhdr *eth = data;
	struct bpf_fib_lookup fib = {};
	int rc;

	if (meta->eth_proto != ETH_P_IP)
		return;
	if ((void *)(eth + 1) > data_end)
		return;

	fib.family = AF_INET;
	fib.ipv4_src = meta->src_ip;
	fib.ipv4_dst = meta->dst_ip;
	fib.ifindex = ctx->ingress_ifindex;

	rc = bpf_fib_lookup(ctx, &fib, sizeof(fib), BPF_FIB_LOOKUP_DIRECT);
	if (rc != BPF_FIB_LKUP_RET_SUCCESS)
		return;

	__builtin_memcpy(eth->h_dest, fib.dmac, ETH_ALEN);
	__builtin_memcpy(eth->h_source, fib.smac, ETH_ALEN);
}

static __always_inline int redirect_out(struct xdp_md *ctx,
					struct pkt_meta *meta)
{
	meta->verdict = PKT_VERDICT_REDIRECT;
	write_test_meta(meta);
	svc_stat_clean(meta);
	l3_rewrite_nexthop(ctx, meta);
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
	int ret;

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

	meta->service_id = service->service_id;
	if (!service->enabled)
		return record_drop(meta, DR_SERVICE_DISABLED);

	ret = ingress_cap_stage(ctx, meta, slot);
	if (ret != FAIR_CONTINUE)
		return ret;
	return whitelist_stage(ctx, meta, slot, service);
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

	meta.frame_len = (__u16)(data_end - data);
	if (test_bad_reason_enabled())
		return record_drop(&meta, (enum drop_reason)DROP_REASON_CAP);
	test_ret = test_whitelist_bloom_probe(&meta);
	if (test_ret >= 0)
		return test_ret;
	test_ret = test_fair_spin_lock_probe(&meta);
	if (test_ret >= 0)
		return test_ret;

	res = parse_eth(&cur, data_end, &meta);
	if (res != PARSE_OK)
		return record_drop(&meta, DR_UNSUPPORTED_ETHERTYPE);

	switch (meta.eth_proto) {
	case ETH_P_IPV6:
		return record_drop(&meta, DR_IPV6_UNSUPPORTED);
	case ETH_P_ARP:
		return redirect_out(ctx, &meta);
	case ETH_P_IP:
		res = parse_ipv4(&cur, data_end, &meta);
		if (res == PARSE_FRAGMENT)
			return record_drop(&meta, DR_FRAGMENT_UNSUPPORTED);
		if (res != PARSE_OK)
			return record_drop(&meta, DR_MALFORMED_IPV4);

		res = parse_l4(&cur, data_end, &meta);
		if (res != PARSE_OK)
			return record_drop(&meta, DR_MALFORMED_IPV4);
		if (node_control_bypass())
			return redirect_out_bypass(ctx, &meta);

		return service_lookup_redirect(ctx, &meta);
	default:
		return record_drop(&meta, DR_UNSUPPORTED_ETHERTYPE);
	}

	return XDP_PASS;
}

char _license[] SEC("license") = "GPL";
