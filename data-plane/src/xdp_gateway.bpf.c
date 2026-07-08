#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

#include "drop_reason.h"
#include "pkt_meta.h"

#ifdef PKT_TEST_HOOKS
struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, 1);
	__type(key, __u32);
	__type(value, struct pkt_meta);
} test_meta_map SEC(".maps");
#endif

SEC("xdp")
int xdp_gateway(struct xdp_md *ctx)
{
	(void)ctx;

	return XDP_PASS;
}

char _license[] SEC("license") = "GPL";
