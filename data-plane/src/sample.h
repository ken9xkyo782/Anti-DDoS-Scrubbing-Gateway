#ifndef XDP_GATEWAY_SAMPLE_H
#define XDP_GATEWAY_SAMPLE_H

#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

#include "drop_event.h"
#include "pkt_meta.h"

#define DROP_RINGBUF_SIZE (256 * 1024)
#define NSEC_PER_SEC 1000000000ULL

struct {
	__uint(type, BPF_MAP_TYPE_RINGBUF);
	__uint(max_entries, DROP_RINGBUF_SIZE);
} drop_ringbuf SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, 1);
	__type(key, __u32);
	__type(value, struct sample_config);
} sample_config SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
	__uint(max_entries, 1);
	__type(key, __u32);
	__type(value, struct sample_bucket_state);
} sample_bucket SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
	__uint(max_entries, SAMPLE_STAT_MAX);
	__type(key, __u32);
	__type(value, __u64);
} sample_stats SEC(".maps");

static __always_inline void bump_sample_stat(enum sample_stat stat)
{
	__u32 key = (__u32)stat;
	__u64 *count = bpf_map_lookup_elem(&sample_stats, &key);

	if (count)
		__sync_fetch_and_add(count, 1);
}

static __always_inline void sample_drop(const struct pkt_meta *meta,
					__u32 reason)
{
	__u32 key = 0;
	struct sample_config *config;
	struct sample_bucket_state *bucket;
	struct drop_event *event;
	__u64 now;

	config = bpf_map_lookup_elem(&sample_config, &key);
	bucket = bpf_map_lookup_elem(&sample_bucket, &key);
	if (!config || !bucket || config->burst == 0)
		return;

	now = bpf_ktime_get_ns();
	if (bucket->last_ns == 0) {
		bucket->tokens = config->burst;
		bucket->last_ns = now;
	} else if (config->rate_per_sec > 0 && now > bucket->last_ns) {
		__u64 elapsed_sec = (now - bucket->last_ns) / NSEC_PER_SEC;

		if (elapsed_sec > 0) {
			__u64 refill = elapsed_sec * config->rate_per_sec;
			__u64 tokens = bucket->tokens + refill;

			bucket->tokens = tokens > config->burst ? config->burst : tokens;
			bucket->last_ns += elapsed_sec * NSEC_PER_SEC;
		}
	}

	if (bucket->tokens == 0) {
		bump_sample_stat(SAMPLE_SUPPRESSED);
		return;
	}
	bucket->tokens--;

	event = bpf_ringbuf_reserve(&drop_ringbuf, sizeof(*event), 0);
	if (!event) {
		bump_sample_stat(SAMPLE_LOST);
		return;
	}

	event->ts_ns = now;
	event->src_ip = meta->src_ip;
	event->dst_ip = meta->dst_ip;
	event->service_id = meta->service_id;
	event->sport = meta->sport;
	event->dport = meta->dport;
	event->reason = (__u8)reason;
	event->ip_proto = meta->ip_proto;
	event->_pad[0] = 0;
	event->_pad[1] = 0;

	bpf_ringbuf_submit(event, 0);
	bump_sample_stat(SAMPLE_EMITTED);
}

#endif
