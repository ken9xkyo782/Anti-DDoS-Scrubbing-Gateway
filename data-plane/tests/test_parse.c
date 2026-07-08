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
#include "xdp_gateway.test.skel.h"

struct test_env {
	struct xdp_gateway_test_bpf *skel;
	int prog_fd;
	int counter_fd;
	int meta_fd;
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
	if (env->prog_fd < 0 || env->counter_fd < 0 || env->meta_fd < 0) {
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

static int reset_maps(struct test_env *env)
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

static int run_frame(struct test_env *env, const struct pkt_frame *frame,
		     __u32 *retval)
{
	struct bpf_test_run_opts opts = {
		.sz = sizeof(opts),
		.data_in = frame->data,
		.data_size_in = frame->len,
		.repeat = 1,
	};
	int err;

	err = reset_maps(env);
	if (err) {
		fprintf(stderr, "failed to reset maps: %s\n", strerror(errno));
		return -1;
	}

	err = bpf_prog_test_run_opts(env->prog_fd, &opts);
	if (err) {
		fprintf(stderr, "BPF_PROG_TEST_RUN failed: %s\n", strerror(errno));
		return -1;
	}

	*retval = opts.retval;
	return 0;
}

static int expect_u32(const char *label, __u32 got, __u32 want)
{
	if (got == want)
		return 0;

	fprintf(stderr, "%s: got %u, want %u\n", label, got, want);
	return -1;
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
	     reason <= DR_MAP_ERROR; reason++) {
		if (expect_counter(env, reason, 0) != 0)
			return -1;
	}

	return 0;
}

static int test_trivial_pass(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_PASS);

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
		err = expect_u32("retval", retval, XDP_PASS);
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
		{ "trivial pass", test_trivial_pass },
		{ "IPv6 drops with counter", test_ipv6_drops_with_counter },
		{ "unsupported EtherType drops with counter",
		  test_unsupported_ethertype_drops_with_counter },
		{ "truncated VLAN drops with unsupported counter",
		  test_truncated_vlan_drops_with_unsupported_counter },
		{ "ARP passes without drop counter",
		  test_arp_passes_without_drop_counter },
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
