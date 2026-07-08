#ifndef XDP_GATEWAY_DROP_EVENT_H
#define XDP_GATEWAY_DROP_EVENT_H

#include <linux/types.h>

struct drop_event {
	__u64 ts_ns;
	__u32 src_ip;
	__u32 dst_ip;
	__u32 service_id;
	__u16 sport;
	__u16 dport;
	__u8 reason;
	__u8 ip_proto;
	__u8 _pad[2];
};

struct sample_config {
	__u64 rate_per_sec;
	__u64 burst;
};

enum sample_stat {
	SAMPLE_EMITTED = 0,
	SAMPLE_SUPPRESSED = 1,
	SAMPLE_LOST = 2,
	SAMPLE_STAT_MAX = 3,
};

#endif
