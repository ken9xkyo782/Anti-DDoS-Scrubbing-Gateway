#ifndef XDP_GATEWAY_SVC_STAT_H
#define XDP_GATEWAY_SVC_STAT_H

#include <linux/types.h>

struct svc_stat {
	__u64 clean_pkts;
	__u64 clean_bytes;
	__u64 drop_pkts;
	__u64 drop_bytes;
	__u64 drop_by_reason[DROP_REASON_CAP];
};

#ifdef __BPF__
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

#include "pkt_meta.h"

/* This preallocated map begins at zero whenever the gateway is reloaded. */
struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_HASH);
	__uint(max_entries, 1024);
	__type(key, __u32);
	__type(value, struct svc_stat);
} svc_stat_map SEC(".maps");

static __always_inline struct svc_stat *svc_stat_get(__u32 dp_id)
{
	struct svc_stat fresh = {};
	struct svc_stat *stat;

	stat = bpf_map_lookup_elem(&svc_stat_map, &dp_id);
	if (stat)
		return stat;

	if (bpf_map_update_elem(&svc_stat_map, &dp_id, &fresh,
				BPF_NOEXIST) != 0) {
		stat = bpf_map_lookup_elem(&svc_stat_map, &dp_id);
		if (!stat)
			return NULL;
	}

	return bpf_map_lookup_elem(&svc_stat_map, &dp_id);
}

static __always_inline void svc_stat_clean(const struct pkt_meta *meta)
{
	struct svc_stat *stat;
	__u32 dp_id = meta->service_id;

	if (!dp_id)
		return;

	stat = svc_stat_get(dp_id);
	if (!stat)
		return;

	__sync_fetch_and_add(&stat->clean_pkts, 1);
	__sync_fetch_and_add(&stat->clean_bytes, meta->frame_len);
}

static __always_inline void svc_stat_drop(const struct pkt_meta *meta,
					  __u32 reason)
{
	struct svc_stat *stat;
	__u32 dp_id = meta->service_id;

	if (!dp_id || reason >= DROP_REASON_CAP)
		return;

	stat = svc_stat_get(dp_id);
	if (!stat)
		return;

	__sync_fetch_and_add(&stat->drop_pkts, 1);
	__sync_fetch_and_add(&stat->drop_bytes, meta->frame_len);
	__sync_fetch_and_add(&stat->drop_by_reason[reason], 1);
}
#endif

#endif
