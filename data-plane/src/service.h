#ifndef XDP_GATEWAY_SERVICE_H
#define XDP_GATEWAY_SERVICE_H

#include <linux/types.h>

#define SERVICE_SLOTS 2

struct service_key {
	__u32 prefixlen;
	__be32 addr;
};

struct service_val {
	__u32 service_id;
	__u8 enabled;
	__u8 _pad[3];
};

struct active_config {
	__u32 active_slot;
	__u32 version;
};

enum pkt_verdict {
	PKT_VERDICT_NONE = 0,
	PKT_VERDICT_REDIRECT = 1,
};

#endif
