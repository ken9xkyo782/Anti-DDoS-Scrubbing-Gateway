#ifndef XDP_GATEWAY_NODE_CONTROL_H
#define XDP_GATEWAY_NODE_CONTROL_H

#include <linux/types.h>

struct node_control {
	__u32 bypass;
	__u32 _reserved;
};

struct bypass_stat {
	__u64 pkts;
	__u64 bytes;
};

#ifdef __BPF__
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

#include "pkt_meta.h"

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, 1);
	__type(key, __u32);
	__type(value, struct node_control);
} node_control SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
	__uint(max_entries, 1);
	__type(key, __u32);
	__type(value, struct bypass_stat);
} bypass_counter SEC(".maps");

static __always_inline int node_control_bypass(void)
{
	__u32 key = 0;
	struct node_control *control;

	control = bpf_map_lookup_elem(&node_control, &key);
	return control && control->bypass;
}

static __always_inline void bypass_count(const struct pkt_meta *meta)
{
	__u32 key = 0;
	struct bypass_stat *stat;

	stat = bpf_map_lookup_elem(&bypass_counter, &key);
	if (!stat)
		return;

	__sync_fetch_and_add(&stat->pkts, 1);
	__sync_fetch_and_add(&stat->bytes, meta->frame_len);
}

static __always_inline int redirect_out_bypass(struct pkt_meta *meta)
{
	meta->verdict = PKT_VERDICT_REDIRECT;
	write_test_meta(meta);
	bypass_count(meta);
	return bpf_redirect_map(&tx_devmap, 0, XDP_DROP);
}
#endif

#endif
