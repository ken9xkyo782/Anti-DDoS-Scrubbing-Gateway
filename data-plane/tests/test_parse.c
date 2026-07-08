#include <errno.h>
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>

#include <bpf/bpf.h>
#include <bpf/libbpf.h>

#include "drop_reason.h"
#include "pkt_build.h"
#include "pkt_meta.h"
#include "service.h"
#include "xdp_gateway.test.skel.h"

#define DEFAULT_SERVICE_ID 42
#define DEFAULT_DST htonl(0x0a000002)

struct test_env {
	struct xdp_gateway_test_bpf *skel;
	int prog_fd;
	int counter_fd;
	int meta_fd;
	int service_inner0_fd;
	int service_inner1_fd;
	int service_map_fd;
	int active_config_fd;
	int tx_devmap_fd;
	int possible_cpus;
};

static int env_open(struct test_env *env)
{
	struct rlimit limit = {
		.rlim_cur = RLIM_INFINITY,
		.rlim_max = RLIM_INFINITY,
	};

	memset(env, 0, sizeof(*env));

	if (setrlimit(RLIMIT_MEMLOCK, &limit) != 0)
		fprintf(stderr, "warning: failed to raise RLIMIT_MEMLOCK: %s\n",
			strerror(errno));

	env->possible_cpus = libbpf_num_possible_cpus();
	if (env->possible_cpus <= 0) {
		fprintf(stderr, "failed to detect possible CPUs\n");
		return -1;
	}

	env->skel = xdp_gateway_test_bpf__open_and_load();
	if (!env->skel) {
		fprintf(stderr, "failed to open/load test BPF skeleton: %s\n",
			strerror(errno));
		return -1;
	}

	env->prog_fd = bpf_program__fd(env->skel->progs.xdp_gateway);
	env->counter_fd = bpf_map__fd(env->skel->maps.counter_map);
	env->meta_fd = bpf_map__fd(env->skel->maps.test_meta_map);
	env->service_inner0_fd = bpf_map__fd(env->skel->maps.service_inner_0);
	env->service_inner1_fd = bpf_map__fd(env->skel->maps.service_inner_1);
	env->service_map_fd = bpf_map__fd(env->skel->maps.service_map);
	env->active_config_fd = bpf_map__fd(env->skel->maps.active_config);
	env->tx_devmap_fd = bpf_map__fd(env->skel->maps.tx_devmap);
	if (env->prog_fd < 0 || env->counter_fd < 0 || env->meta_fd < 0 ||
	    env->service_inner0_fd < 0 || env->service_inner1_fd < 0 ||
	    env->service_map_fd < 0 || env->active_config_fd < 0 ||
	    env->tx_devmap_fd < 0) {
		fprintf(stderr, "failed to resolve BPF fds\n");
		xdp_gateway_test_bpf__destroy(env->skel);
		return -1;
	}

	return 0;
}

static void env_close(struct test_env *env)
{
	xdp_gateway_test_bpf__destroy(env->skel);
}

static int reset_observability(struct test_env *env)
{
	__u64 *zero_counts;
	struct pkt_meta zero_meta = {};
	__u32 key;
	int err = 0;

	zero_counts = calloc(env->possible_cpus, sizeof(*zero_counts));
	if (!zero_counts)
		return -1;

	for (key = 0; key < DROP_REASON_CAP; key++) {
		if (bpf_map_update_elem(env->counter_fd, &key, zero_counts, 0) != 0) {
			err = -1;
			break;
		}
	}

	key = 0;
	if (!err && bpf_map_update_elem(env->meta_fd, &key, &zero_meta, 0) != 0)
		err = -1;

	free(zero_counts);
	return err;
}

static int clear_service_map(int fd)
{
	struct service_key key;

	while (bpf_map_get_next_key(fd, NULL, &key) == 0) {
		if (bpf_map_delete_elem(fd, &key) != 0)
			return -1;
	}

	return errno == ENOENT ? 0 : -1;
}

static int reset_config(struct test_env *env)
{
	struct active_config config = {};
	__u32 key = 0;

	if (clear_service_map(env->service_inner0_fd) != 0 ||
	    clear_service_map(env->service_inner1_fd) != 0)
		return -1;

	if (bpf_map_update_elem(env->active_config_fd, &key, &config, 0) != 0)
		return -1;

	if (bpf_map_delete_elem(env->tx_devmap_fd, &key) != 0 &&
	    errno != ENOENT)
		return -1;

	return 0;
}

static int reset_maps(struct test_env *env)
{
	if (reset_observability(env) != 0)
		return -1;

	return reset_config(env);
}

static int service_fd_for_slot(struct test_env *env, __u32 slot)
{
	if (slot == 0)
		return env->service_inner0_fd;
	if (slot == 1)
		return env->service_inner1_fd;

	errno = EINVAL;
	return -1;
}

static int seed_service(struct test_env *env, __u32 slot, __be32 addr,
			__u32 prefixlen, __u32 service_id, __u8 enabled)
{
	struct service_key key = {
		.prefixlen = prefixlen,
		.addr = addr,
	};
	struct service_val val = {
		.service_id = service_id,
		.enabled = enabled,
	};
	int fd = service_fd_for_slot(env, slot);

	if (fd < 0)
		return -1;

	return bpf_map_update_elem(fd, &key, &val, BPF_ANY);
}

static int set_active(struct test_env *env, __u32 slot, __u32 version)
{
	struct active_config config = {
		.active_slot = slot,
		.version = version,
	};
	__u32 key = 0;

	return bpf_map_update_elem(env->active_config_fd, &key, &config, 0);
}

static int read_counter(struct test_env *env, enum drop_reason reason, __u64 *sum)
{
	__u64 *values;
	__u32 key = (__u32)reason;
	int err;

	values = calloc(env->possible_cpus, sizeof(*values));
	if (!values)
		return -1;

	err = bpf_map_lookup_elem(env->counter_fd, &key, values);
	if (err == 0) {
		*sum = 0;
		for (int i = 0; i < env->possible_cpus; i++)
			*sum += values[i];
	}

	free(values);
	return err;
}

static int __attribute__((unused)) read_meta(struct test_env *env,
					    struct pkt_meta *meta)
{
	__u32 key = 0;

	return bpf_map_lookup_elem(env->meta_fd, &key, meta);
}

static int run_frame_current_maps(struct test_env *env,
				  const struct pkt_frame *frame,
				  __u32 *retval)
{
	struct bpf_test_run_opts opts = {
		.sz = sizeof(opts),
		.data_in = frame->data,
		.data_size_in = frame->len,
		.repeat = 1,
	};
	int err;

	err = bpf_prog_test_run_opts(env->prog_fd, &opts);
	if (err) {
		fprintf(stderr, "BPF_PROG_TEST_RUN failed: %s\n", strerror(errno));
		return -1;
	}

	*retval = opts.retval;
	return 0;
}

static int run_frame(struct test_env *env, const struct pkt_frame *frame,
		     __u32 *retval)
{
	int err;

	err = reset_maps(env);
	if (err) {
		fprintf(stderr, "failed to reset maps: %s\n", strerror(errno));
		return -1;
	}

	return run_frame_current_maps(env, frame, retval);
}

static int run_enabled_service_frame(struct test_env *env,
				     const struct pkt_frame *frame,
				     __u32 *retval)
{
	if (reset_maps(env) != 0 ||
	    seed_service(env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1) != 0 ||
	    set_active(env, 0, 1) != 0)
		return -1;

	return run_frame_current_maps(env, frame, retval);
}

static int expect_u32(const char *label, __u32 got, __u32 want)
{
	if (got == want)
		return 0;

	fprintf(stderr, "%s: got %u, want %u\n", label, got, want);
	return -1;
}

static int expect_u16(const char *label, __u16 got, __u16 want)
{
	if (got == want)
		return 0;

	fprintf(stderr, "%s: got %u, want %u\n", label, got, want);
	return -1;
}

static int expect_u8(const char *label, __u8 got, __u8 want)
{
	if (got == want)
		return 0;

	fprintf(stderr, "%s: got %u, want %u\n", label, got, want);
	return -1;
}

static int expect_fd(const char *label, int fd)
{
	if (fd >= 0)
		return 0;

	fprintf(stderr, "%s: invalid fd %d\n", label, fd);
	return -1;
}

static int expect_redirect_meta(struct test_env *env, struct pkt_meta *meta,
				__u32 service_id, __u8 active_slot)
{
	if (read_meta(env, meta) != 0)
		return -1;
	if (expect_u8("verdict", meta->verdict, PKT_VERDICT_REDIRECT) != 0)
		return -1;
	if (expect_u32("service_id", meta->service_id, service_id) != 0)
		return -1;
	return expect_u8("active_slot", meta->active_slot, active_slot);
}

static int expect_counter(struct test_env *env, enum drop_reason reason,
			  __u64 want)
{
	__u64 got = 0;

	if (read_counter(env, reason, &got) != 0) {
		fprintf(stderr, "counter[%u]: read failed: %s\n", reason,
			strerror(errno));
		return -1;
	}

	if (got == want)
		return 0;

	fprintf(stderr, "counter[%u]: got %llu, want %llu\n", reason,
		(unsigned long long)got, (unsigned long long)want);
	return -1;
}

static int expect_all_drop_counters_zero(struct test_env *env)
{
	for (enum drop_reason reason = DR_IPV6_UNSUPPORTED;
	     reason <= DR_SERVICE_DISABLED; reason++) {
		if (expect_counter(env, reason, 0) != 0)
			return -1;
	}

	return 0;
}

static int test_valid_other_ipv4_passes_with_zero_ports(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_GRE, 0, 5) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_enabled_service_frame(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("ip_proto", meta.ip_proto, IPPROTO_GRE);
	if (!err)
		err = expect_u16("sport", meta.sport, 0);
	if (!err)
		err = expect_u16("dport", meta.dport, 0);

	env_close(&env);
	return err;
}

static int test_config_maps_load(void)
{
	struct test_env env;
	int err;

	err = env_open(&env);
	if (err)
		return -1;

	if (!err)
		err = expect_fd("service_inner_0", env.service_inner0_fd);
	if (!err)
		err = expect_fd("service_inner_1", env.service_inner1_fd);
	if (!err)
		err = expect_fd("service_map", env.service_map_fd);
	if (!err)
		err = expect_fd("active_config", env.active_config_fd);
	if (!err)
		err = expect_fd("tx_devmap", env.tx_devmap_fd);

	env_close(&env);
	return err;
}

static int build_default_udp_frame(struct pkt_frame *frame)
{
	pkt_frame_init(frame);
	return build_eth(frame, ETH_P_IP) ||
	       build_ipv4(frame, IPPROTO_UDP, 0, 5) ||
	       build_udp(frame, 1234, 53);
}

static int test_service_miss_drops(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_MISS, 1);

	env_close(&env);
	return err;
}

static int test_service_disabled_drops(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, 7, 0);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_DISABLED, 1);

	env_close(&env);
	return err;
}

static int test_enabled_service_sets_redirect_meta(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_enabled_service_frame(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_all_drop_counters_zero(&env);

	env_close(&env);
	return err;
}

static int test_cidr_service_matches_host(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, htonl(0x0a000000), 24, 99, 1);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, 99, 0);
	if (!err)
		err = expect_all_drop_counters_zero(&env);

	env_close(&env);
	return err;
}

static int test_slot_pin_flip_changes_service_view(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, 11, 1);
	if (!err)
		err = seed_service(&env, 1, DEFAULT_DST, 32, 11, 0);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, 11, 0);
	if (!err)
		err = expect_all_drop_counters_zero(&env);
	if (!err)
		err = reset_observability(&env);
	if (!err)
		err = set_active(&env, 1, 2);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_DISABLED, 1);

	env_close(&env);
	return err;
}

static int test_empty_config_drops_service_miss(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_MISS, 1);

	env_close(&env);
	return err;
}

static int test_invalid_active_slot_drops_map_error(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_active(&env, SERVICE_SLOTS, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MAP_ERROR, 1);

	env_close(&env);
	return err;
}

static int test_esp_ipv4_passes_with_zero_ports(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_ESP, 0, 5) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_enabled_service_frame(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("ip_proto", meta.ip_proto, IPPROTO_ESP);
	if (!err)
		err = expect_u16("sport", meta.sport, 0);
	if (!err)
		err = expect_u16("dport", meta.dport, 0);

	env_close(&env);
	return err;
}

static int test_ipv4_bad_version_drops_malformed(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct iphdr *iph;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_UDP, 0, 5) != 0)
		return -1;

	iph = (struct iphdr *)(frame.data + frame.ipv4_off);
	iph->version = 5;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MALFORMED_IPV4, 1);

	env_close(&env);
	return err;
}

static int test_ipv4_bad_ihl_drops_malformed(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_UDP, 0, 4) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MALFORMED_IPV4, 1);

	env_close(&env);
	return err;
}

static int test_ipv4_truncated_header_drops_malformed(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 || !pkt_append(&frame, 10))
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MALFORMED_IPV4, 1);

	env_close(&env);
	return err;
}

static int test_ipv4_total_length_beyond_frame_drops_malformed(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct iphdr *iph;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_UDP, 0, 5) != 0)
		return -1;

	iph = (struct iphdr *)(frame.data + frame.ipv4_off);
	iph->tot_len = htons(128);

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MALFORMED_IPV4, 1);

	env_close(&env);
	return err;
}

static int test_ipv4_first_fragment_drops_fragment_counter(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_UDP, IPV4_MF, 5) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_FRAGMENT_UNSUPPORTED, 1);

	env_close(&env);
	return err;
}

static int test_ipv4_later_fragment_drops_fragment_counter(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_UDP, 1, 5) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_FRAGMENT_UNSUPPORTED, 1);

	env_close(&env);
	return err;
}

static int test_tcp_ports_written_to_meta(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_TCP, 0, 5) != 0 ||
	    build_tcp(&frame, 12345, 443) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_enabled_service_frame(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u16("sport", meta.sport, htons(12345));
	if (!err)
		err = expect_u16("dport", meta.dport, htons(443));
	if (!err)
		err = expect_u32("src_ip", meta.src_ip, htonl(0x0a000001));
	if (!err)
		err = expect_u16("l3_off", meta.l3_off, sizeof(struct ethhdr));
	if (!err)
		err = expect_u16("l4_off", meta.l4_off,
				 sizeof(struct ethhdr) + sizeof(struct iphdr));

	env_close(&env);
	return err;
}

static int test_udp_ports_written_to_meta(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_UDP, 0, 5) != 0 ||
	    build_udp(&frame, 1234, 53) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_enabled_service_frame(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u16("sport", meta.sport, htons(1234));
	if (!err)
		err = expect_u16("dport", meta.dport, htons(53));
	if (!err)
		err = expect_u32("dst_ip", meta.dst_ip, htonl(0x0a000002));

	env_close(&env);
	return err;
}

static int test_icmp_type_code_written_to_meta(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_ICMP, 0, 5) != 0 ||
	    build_icmp(&frame, 8, 0) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_enabled_service_frame(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("icmp_type", meta.icmp_type, 8);
	if (!err)
		err = expect_u8("icmp_code", meta.icmp_code, 0);

	env_close(&env);
	return err;
}

static int test_truncated_udp_drops_malformed(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_UDP, 0, 5) != 0 ||
	    !pkt_append(&frame, 4) || pkt_fix_ipv4_tot_len(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MALFORMED_IPV4, 1);

	env_close(&env);
	return err;
}

static int test_truncated_tcp_drops_malformed(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_TCP, 0, 5) != 0 ||
	    !pkt_append(&frame, 10) || pkt_fix_ipv4_tot_len(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MALFORMED_IPV4, 1);

	env_close(&env);
	return err;
}

static int test_single_vlan_ipv4_passes_with_meta(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_vlan(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_UDP, 0, 5) != 0 ||
	    build_udp(&frame, 5555, 8080) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_enabled_service_frame(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("vlan_depth", meta.vlan_depth, 1);
	if (!err)
		err = expect_u16("eth_proto", meta.eth_proto, ETH_P_IP);
	if (!err)
		err = expect_u16("dport", meta.dport, htons(8080));

	env_close(&env);
	return err;
}

static int test_qinq_ipv4_passes_with_meta(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_qinq(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_TCP, 0, 5) != 0 ||
	    build_tcp(&frame, 4444, 443) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_enabled_service_frame(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("vlan_depth", meta.vlan_depth, 2);
	if (!err)
		err = expect_u16("eth_proto", meta.eth_proto, ETH_P_IP);
	if (!err)
		err = expect_u16("sport", meta.sport, htons(4444));

	env_close(&env);
	return err;
}

static int test_triple_vlan_drops_unsupported(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_8021AD) != 0 ||
	    append_vlan_tag(&frame, ETH_P_8021Q) != 0 ||
	    append_vlan_tag(&frame, ETH_P_8021Q) != 0 ||
	    append_vlan_tag(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_UDP, 0, 5) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_UNSUPPORTED_ETHERTYPE, 1);

	env_close(&env);
	return err;
}

static int test_vlan_ipv6_drops_ipv6_counter(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_vlan(&frame, ETH_P_IPV6) != 0 || build_ipv6(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_IPV6_UNSUPPORTED, 1);

	env_close(&env);
	return err;
}

static int test_ipv6_drops_with_counter(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IPV6) != 0 || build_ipv6(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_IPV6_UNSUPPORTED, 1);

	env_close(&env);
	return err;
}

static int test_unsupported_ethertype_drops_with_counter(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, 0x0000) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_UNSUPPORTED_ETHERTYPE, 1);

	env_close(&env);
	return err;
}

static int test_truncated_vlan_drops_with_unsupported_counter(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_8021Q) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_UNSUPPORTED_ETHERTYPE, 1);

	env_close(&env);
	return err;
}

static int test_arp_passes_without_drop_counter(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_ARP) != 0 || build_arp(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = read_meta(&env, &meta);
	if (!err)
		err = expect_u8("verdict", meta.verdict, PKT_VERDICT_REDIRECT);
	if (!err)
		err = expect_all_drop_counters_zero(&env);

	env_close(&env);
	return err;
}

struct test_case {
	const char *name;
	int (*run)(void);
};

int main(void)
{
	const struct test_case tests[] = {
		{ "config maps load", test_config_maps_load },
		{ "service miss drops", test_service_miss_drops },
		{ "service disabled drops", test_service_disabled_drops },
		{ "enabled service sets redirect meta",
		  test_enabled_service_sets_redirect_meta },
		{ "CIDR service matches host", test_cidr_service_matches_host },
		{ "slot pin flip changes service view",
		  test_slot_pin_flip_changes_service_view },
		{ "empty config drops service miss",
		  test_empty_config_drops_service_miss },
		{ "invalid active slot drops map error",
		  test_invalid_active_slot_drops_map_error },
		{ "valid other IPv4 passes with zero ports",
		  test_valid_other_ipv4_passes_with_zero_ports },
		{ "ESP IPv4 passes with zero ports",
		  test_esp_ipv4_passes_with_zero_ports },
		{ "IPv6 drops with counter", test_ipv6_drops_with_counter },
		{ "unsupported EtherType drops with counter",
		  test_unsupported_ethertype_drops_with_counter },
		{ "truncated VLAN drops with unsupported counter",
		  test_truncated_vlan_drops_with_unsupported_counter },
		{ "ARP passes without drop counter",
		  test_arp_passes_without_drop_counter },
		{ "IPv4 bad version drops malformed",
		  test_ipv4_bad_version_drops_malformed },
		{ "IPv4 bad IHL drops malformed",
		  test_ipv4_bad_ihl_drops_malformed },
		{ "IPv4 truncated header drops malformed",
		  test_ipv4_truncated_header_drops_malformed },
		{ "IPv4 total length beyond frame drops malformed",
		  test_ipv4_total_length_beyond_frame_drops_malformed },
		{ "IPv4 first fragment drops fragment",
		  test_ipv4_first_fragment_drops_fragment_counter },
		{ "IPv4 later fragment drops fragment",
		  test_ipv4_later_fragment_drops_fragment_counter },
		{ "TCP ports written to meta", test_tcp_ports_written_to_meta },
		{ "UDP ports written to meta", test_udp_ports_written_to_meta },
		{ "ICMP type/code written to meta",
		  test_icmp_type_code_written_to_meta },
		{ "truncated UDP drops malformed",
		  test_truncated_udp_drops_malformed },
		{ "truncated TCP drops malformed",
		  test_truncated_tcp_drops_malformed },
		{ "single VLAN IPv4 passes with meta",
		  test_single_vlan_ipv4_passes_with_meta },
		{ "QinQ IPv4 passes with meta", test_qinq_ipv4_passes_with_meta },
		{ "triple VLAN drops unsupported",
		  test_triple_vlan_drops_unsupported },
		{ "VLAN IPv6 drops IPv6 counter",
		  test_vlan_ipv6_drops_ipv6_counter },
	};
	size_t passed = 0;
	size_t count = sizeof(tests) / sizeof(tests[0]);

	for (size_t i = 0; i < count; i++) {
		if (tests[i].run() != 0) {
			fprintf(stderr, "not ok %zu - %s\n", i + 1, tests[i].name);
			return 1;
		}
		printf("ok %zu - %s\n", i + 1, tests[i].name);
		passed++;
	}

	printf("%zu passed\n", passed);
	return 0;
}
