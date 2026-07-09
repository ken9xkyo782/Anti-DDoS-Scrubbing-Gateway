#define _GNU_SOURCE

#include <errno.h>
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>

#include <bpf/bpf.h>
#include <bpf/libbpf.h>

#include "drop_reason.h"
#include "drop_event.h"
#include "pkt_build.h"
#include "pkt_meta.h"
#include "rules.h"
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
	int rule_block0_fd;
	int rule_block1_fd;
	int rule_block_map_fd;
	int rate_limit_state_fd;
	int rl_config_fd;
	int active_config_fd;
	int tx_devmap_fd;
	int trigger_fd;
	int ringbuf_fd;
	int sample_config_fd;
	int sample_bucket_fd;
	int sample_stats_fd;
	int possible_cpus;
};

static int build_default_udp_frame(struct pkt_frame *frame);

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

	env->skel = xdp_gateway_test_bpf__open();
	if (!env->skel) {
		fprintf(stderr, "failed to open test BPF skeleton: %s\n",
			strerror(errno));
		return -1;
	}

	env->skel->rodata->rl_ncpus = (__u32)env->possible_cpus;
	if (xdp_gateway_test_bpf__load(env->skel) != 0) {
		fprintf(stderr, "failed to load test BPF skeleton: %s\n",
			strerror(errno));
		xdp_gateway_test_bpf__destroy(env->skel);
		return -1;
	}

	env->prog_fd = bpf_program__fd(env->skel->progs.xdp_gateway);
	env->counter_fd = bpf_map__fd(env->skel->maps.counter_map);
	env->meta_fd = bpf_map__fd(env->skel->maps.test_meta_map);
	env->service_inner0_fd = bpf_map__fd(env->skel->maps.service_inner_0);
	env->service_inner1_fd = bpf_map__fd(env->skel->maps.service_inner_1);
	env->service_map_fd = bpf_map__fd(env->skel->maps.service_map);
	env->rule_block0_fd = bpf_map__fd(env->skel->maps.rule_block_0);
	env->rule_block1_fd = bpf_map__fd(env->skel->maps.rule_block_1);
	env->rule_block_map_fd = bpf_map__fd(env->skel->maps.rule_block_map);
	env->rate_limit_state_fd = bpf_map__fd(env->skel->maps.rate_limit_state);
	env->rl_config_fd = bpf_map__fd(env->skel->maps.rl_config);
	env->active_config_fd = bpf_map__fd(env->skel->maps.active_config);
	env->tx_devmap_fd = bpf_map__fd(env->skel->maps.tx_devmap);
	env->trigger_fd = bpf_map__fd(env->skel->maps.test_trigger_map);
	env->ringbuf_fd = bpf_map__fd(env->skel->maps.drop_ringbuf);
	env->sample_config_fd = bpf_map__fd(env->skel->maps.sample_config);
	env->sample_bucket_fd = bpf_map__fd(env->skel->maps.sample_bucket);
	env->sample_stats_fd = bpf_map__fd(env->skel->maps.sample_stats);
	if (env->prog_fd < 0 || env->counter_fd < 0 || env->meta_fd < 0 ||
	    env->service_inner0_fd < 0 || env->service_inner1_fd < 0 ||
	    env->service_map_fd < 0 || env->rule_block0_fd < 0 ||
	    env->rule_block1_fd < 0 || env->rule_block_map_fd < 0 ||
	    env->rate_limit_state_fd < 0 || env->rl_config_fd < 0 ||
	    env->active_config_fd < 0 || env->tx_devmap_fd < 0 ||
	    env->trigger_fd < 0 ||
	    env->ringbuf_fd < 0 || env->sample_config_fd < 0 ||
	    env->sample_bucket_fd < 0 || env->sample_stats_fd < 0) {
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
	struct sample_bucket_state *zero_buckets;
	struct pkt_meta zero_meta = {};
	struct sample_config zero_config = {};
	__u32 zero_trigger = 0;
	__u32 key;
	int err = 0;

	zero_counts = calloc(env->possible_cpus, sizeof(*zero_counts));
	if (!zero_counts)
		return -1;

	zero_buckets = calloc(env->possible_cpus, sizeof(*zero_buckets));
	if (!zero_buckets) {
		free(zero_counts);
		return -1;
	}

	for (key = 0; key < DROP_REASON_CAP; key++) {
		if (bpf_map_update_elem(env->counter_fd, &key, zero_counts, 0) != 0) {
			err = -1;
			break;
		}
	}

	key = 0;
	if (!err && bpf_map_update_elem(env->meta_fd, &key, &zero_meta, 0) != 0)
		err = -1;
	if (!err && bpf_map_update_elem(env->trigger_fd, &key, &zero_trigger, 0) != 0)
		err = -1;
	if (!err && bpf_map_update_elem(env->sample_config_fd, &key, &zero_config, 0) != 0)
		err = -1;
	if (!err && bpf_map_update_elem(env->sample_bucket_fd, &key, zero_buckets, 0) != 0)
		err = -1;

	for (key = 0; !err && key < SAMPLE_STAT_MAX; key++) {
		if (bpf_map_update_elem(env->sample_stats_fd, &key, zero_counts, 0) != 0)
			err = -1;
	}

	free(zero_buckets);
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

static int clear_rule_block_map(int fd)
{
	__u32 key;

	while (bpf_map_get_next_key(fd, NULL, &key) == 0) {
		if (bpf_map_delete_elem(fd, &key) != 0)
			return -1;
	}

	return errno == ENOENT ? 0 : -1;
}

static int clear_rate_limit_state(int fd)
{
	struct rl_key key;

	while (bpf_map_get_next_key(fd, NULL, &key) == 0) {
		if (bpf_map_delete_elem(fd, &key) != 0)
			return -1;
	}

	return errno == ENOENT ? 0 : -1;
}

static int reset_rate_limit(struct test_env *env)
{
	struct rl_config config = {};
	__u32 key = 0;

	if (clear_rate_limit_state(env->rate_limit_state_fd) != 0)
		return -1;
	return bpf_map_update_elem(env->rl_config_fd, &key, &config, 0);
}

static int reset_config(struct test_env *env)
{
	struct active_config config = {};
	__u32 key = 0;

	if (clear_service_map(env->service_inner0_fd) != 0 ||
	    clear_service_map(env->service_inner1_fd) != 0 ||
	    clear_rule_block_map(env->rule_block0_fd) != 0 ||
	    clear_rule_block_map(env->rule_block1_fd) != 0)
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

	if (reset_rate_limit(env) != 0)
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

static int rule_block_fd_for_slot(struct test_env *env, __u32 slot)
{
	if (slot == 0)
		return env->rule_block0_fd;
	if (slot == 1)
		return env->rule_block1_fd;

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

static struct rule_entry allow_rule(__u8 proto, __u16 src_lo, __u16 src_hi,
				    __u16 dst_lo, __u16 dst_hi, __u8 flags)
{
	struct rule_entry rule = {
		.src_lo = src_lo,
		.src_hi = src_hi,
		.dst_lo = dst_lo,
		.dst_hi = dst_hi,
		.proto = proto,
		.flags = flags,
	};

	return rule;
}

static struct rule_entry match_all_rule(void)
{
	return allow_rule(RULE_PROTO_ANY, 0, UINT16_MAX, 0, UINT16_MAX,
			  RULE_F_ENABLED);
}

static struct rule_entry default_udp_rule(void)
{
	return allow_rule(IPPROTO_UDP, 0, UINT16_MAX, 53, 53,
			  RULE_F_ENABLED);
}

static int seed_rule_block(struct test_env *env, __u32 slot, __u32 service_id,
			   const struct rule_block *block)
{
	int fd = rule_block_fd_for_slot(env, slot);

	if (fd < 0)
		return -1;

	return bpf_map_update_elem(fd, &service_id, block, BPF_ANY);
}

static int seed_match_all_rule_block(struct test_env *env, __u32 slot,
				     __u32 service_id)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 1,
	};

	block.rules[0] = match_all_rule();
	return seed_rule_block(env, slot, service_id, &block);
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

static int set_sample_config(struct test_env *env, __u64 rate_per_sec,
			     __u64 burst)
{
	struct sample_config config = {
		.rate_per_sec = rate_per_sec,
		.burst = burst,
	};
	__u32 key = 0;

	return bpf_map_update_elem(env->sample_config_fd, &key, &config, 0);
}

static int set_rl_config(struct test_env *env, __u32 test_no_refill)
{
	struct rl_config config = {
		.test_no_refill = test_no_refill,
	};
	__u32 key = 0;

	return bpf_map_update_elem(env->rl_config_fd, &key, &config, 0);
}

static int set_bad_reason_trigger(struct test_env *env, __u32 enabled)
{
	__u32 key = 0;

	return bpf_map_update_elem(env->trigger_fd, &key, &enabled, 0);
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

static int read_sample_stat(struct test_env *env, enum sample_stat stat,
			    __u64 *sum)
{
	__u64 *values;
	__u32 key = (__u32)stat;
	int err;

	values = calloc(env->possible_cpus, sizeof(*values));
	if (!values)
		return -1;

	err = bpf_map_lookup_elem(env->sample_stats_fd, &key, values);
	if (err == 0) {
		*sum = 0;
		for (int i = 0; i < env->possible_cpus; i++)
			*sum += values[i];
	}

	free(values);
	return err;
}

static int read_bucket_cpu0(struct test_env *env, __u32 service_id,
			    __u32 rule_idx, struct rl_bucket *bucket)
{
	struct rl_bucket *values;
	struct rl_key key = {
		.service_id = service_id,
		.rule_idx = rule_idx,
	};
	int err;

	values = calloc(env->possible_cpus, sizeof(*values));
	if (!values)
		return -1;

	err = bpf_map_lookup_elem(env->rate_limit_state_fd, &key, values);
	if (err == 0)
		*bucket = values[0];

	free(values);
	return err;
}

static int __attribute__((unused)) read_meta(struct test_env *env,
					    struct pkt_meta *meta)
{
	__u32 key = 0;

	return bpf_map_lookup_elem(env->meta_fd, &key, meta);
}

struct event_capture {
	struct drop_event events[8];
	size_t count;
	size_t bad_len;
};

static int capture_drop_event(void *ctx, void *data, size_t len)
{
	struct event_capture *capture = ctx;

	if (len != sizeof(struct drop_event)) {
		capture->bad_len = len;
		return 0;
	}

	if (capture->count < sizeof(capture->events) / sizeof(capture->events[0]))
		memcpy(&capture->events[capture->count], data,
		       sizeof(capture->events[capture->count]));
	capture->count++;
	return 0;
}

static int consume_drop_events(struct test_env *env, struct event_capture *capture)
{
	struct ring_buffer *ring;
	int err;

	memset(capture, 0, sizeof(*capture));
	ring = ring_buffer__new(env->ringbuf_fd, capture_drop_event, capture, NULL);
	if (!ring)
		return -1;

	err = ring_buffer__consume(ring);
	ring_buffer__free(ring);
	if (err < 0)
		return -1;
	return 0;
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
	    seed_match_all_rule_block(env, 0, DEFAULT_SERVICE_ID) != 0 ||
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

static int expect_sample_stat(struct test_env *env, enum sample_stat stat,
			      __u64 want)
{
	__u64 got = 0;

	if (read_sample_stat(env, stat, &got) != 0) {
		fprintf(stderr, "sample_stat[%u]: read failed: %s\n", stat,
			strerror(errno));
		return -1;
	}

	if (got == want)
		return 0;

	fprintf(stderr, "sample_stat[%u]: got %llu, want %llu\n", stat,
		(unsigned long long)got, (unsigned long long)want);
	return -1;
}

static int expect_all_drop_counters_zero(struct test_env *env)
{
	for (enum drop_reason reason = DR_IPV6_UNSUPPORTED;
	     reason < DROP_REASON_COUNT; reason++) {
		if (expect_counter(env, reason, 0) != 0)
			return -1;
	}

	return 0;
}

static int expect_reason_zero(struct test_env *env, enum drop_reason reason)
{
	return expect_counter(env, reason, 0);
}

static int test_drop_reason_abi_exposes_16_slots(void)
{
	struct test_env env;
	int err;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	for (enum drop_reason reason = DR_IPV6_UNSUPPORTED;
	     !err && reason < DROP_REASON_COUNT; reason++)
		err = expect_counter(&env, reason, 0);

	if (!err)
		err = expect_reason_zero(&env, DR_BOGON_DROP);
	if (!err)
		err = expect_reason_zero(&env, DR_UDP_AMPLIFICATION_DROP);
	if (!err)
		err = expect_reason_zero(&env, DR_BLACKLIST_DROP);
	if (!err)
		err = expect_reason_zero(&env, DR_NOT_ALLOWED);
	if (!err)
		err = expect_reason_zero(&env, DR_RATE_LIMIT_DROP);
	if (!err)
		err = expect_reason_zero(&env, DR_SERVICE_CEILING_DROP);
	if (!err)
		err = expect_reason_zero(&env, DR_CONGESTION_DROP);
	if (!err)
		err = expect_reason_zero(&env, DR_INGRESS_CAP_DROP);
	if (!err)
		err = expect_reason_zero(&env, DR_VIP_CEILING_DROP);

	env_close(&env);
	return err;
}

static int expect_default_udp_miss_event(const struct drop_event *event)
{
	if (expect_u8("event.reason", event->reason, DR_SERVICE_MISS) != 0)
		return -1;
	if (expect_u32("event.src_ip", event->src_ip, htonl(0x0a000001)) != 0)
		return -1;
	if (expect_u32("event.dst_ip", event->dst_ip, DEFAULT_DST) != 0)
		return -1;
	if (expect_u32("event.service_id", event->service_id, 0) != 0)
		return -1;
	if (expect_u16("event.sport", event->sport, htons(1234)) != 0)
		return -1;
	if (expect_u16("event.dport", event->dport, htons(53)) != 0)
		return -1;
	return expect_u8("event.ip_proto", event->ip_proto, IPPROTO_UDP);
}

static int test_ringbuf_delivers_after_test_run(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct event_capture capture;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IPV6) != 0 || build_ipv6(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_sample_config(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = consume_drop_events(&env, &capture);
	if (!err)
		err = expect_u32("captured events", capture.count, 1);
	if (!err)
		err = expect_u32("bad event length", capture.bad_len, 0);
	if (!err)
		err = expect_u8("event.reason", capture.events[0].reason,
				DR_IPV6_UNSUPPORTED);
	if (!err)
		err = expect_sample_stat(&env, SAMPLE_EMITTED, 1);
	if (!err)
		err = expect_sample_stat(&env, SAMPLE_SUPPRESSED, 0);
	if (!err)
		err = expect_sample_stat(&env, SAMPLE_LOST, 0);

	env_close(&env);
	return err;
}

static int test_sampling_disabled_keeps_counters_exact(void)
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
	if (!err)
		err = expect_sample_stat(&env, SAMPLE_EMITTED, 0);
	if (!err)
		err = expect_sample_stat(&env, SAMPLE_SUPPRESSED, 0);
	if (!err)
		err = expect_sample_stat(&env, SAMPLE_LOST, 0);

	env_close(&env);
	return err;
}

static int test_sampling_budget_limits_events_and_keeps_content(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct event_capture capture;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_sample_config(&env, 0, 2);
	for (int i = 0; !err && i < 5; i++) {
		err = run_frame_current_maps(&env, &frame, &retval);
		if (!err)
			err = expect_u32("retval", retval, XDP_DROP);
	}

	if (!err)
		err = consume_drop_events(&env, &capture);
	if (!err)
		err = expect_u32("captured events", capture.count, 2);
	if (!err)
		err = expect_u32("bad event length", capture.bad_len, 0);
	if (!err)
		err = expect_default_udp_miss_event(&capture.events[0]);
	if (!err)
		err = expect_default_udp_miss_event(&capture.events[1]);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_MISS, 5);
	if (!err)
		err = expect_sample_stat(&env, SAMPLE_EMITTED, 2);
	if (!err)
		err = expect_sample_stat(&env, SAMPLE_SUPPRESSED, 3);
	if (!err)
		err = expect_sample_stat(&env, SAMPLE_LOST, 0);

	env_close(&env);
	return err;
}

static int test_bad_reason_clamps_to_map_error(void)
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
		err = set_bad_reason_trigger(&env, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MAP_ERROR, 1);

	env_close(&env);
	return err;
}

static int test_valid_other_ipv4_drops_not_allowed(void)
{
	struct pkt_frame frame;
	struct test_env env;
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
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_NOT_ALLOWED, 1);

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
		err = expect_fd("rule_block_0", env.rule_block0_fd);
	if (!err)
		err = expect_fd("rule_block_1", env.rule_block1_fd);
	if (!err)
		err = expect_fd("rule_block_map", env.rule_block_map_fd);
	if (!err)
		err = expect_fd("rate_limit_state", env.rate_limit_state_fd);
	if (!err)
		err = expect_fd("rl_config", env.rl_config_fd);
	if (!err)
		err = expect_fd("active_config", env.active_config_fd);
	if (!err)
		err = expect_fd("tx_devmap", env.tx_devmap_fd);
	if (!err)
		err = expect_fd("test_trigger_map", env.trigger_fd);
	if (!err)
		err = expect_fd("drop_ringbuf", env.ringbuf_fd);
	if (!err)
		err = expect_fd("sample_config", env.sample_config_fd);
	if (!err)
		err = expect_fd("sample_bucket", env.sample_bucket_fd);
	if (!err)
		err = expect_fd("sample_stats", env.sample_stats_fd);

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

static int build_udp_frame_ports(struct pkt_frame *frame, __u16 sport,
				 __u16 dport)
{
	pkt_frame_init(frame);
	return build_eth(frame, ETH_P_IP) ||
	       build_ipv4(frame, IPPROTO_UDP, 0, 5) ||
	       build_udp(frame, sport, dport);
}

static int build_tcp_frame_ports(struct pkt_frame *frame, __u16 sport,
				 __u16 dport)
{
	pkt_frame_init(frame);
	return build_eth(frame, ETH_P_IP) ||
	       build_ipv4(frame, IPPROTO_TCP, 0, 5) ||
	       build_tcp(frame, sport, dport);
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
		err = expect_u8("rule_idx", meta.rule_idx, 0);
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
		err = seed_match_all_rule_block(&env, 0, 99);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, 99, 0);
	if (!err)
		err = expect_u8("rule_idx", meta.rule_idx, 0);
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
		err = seed_match_all_rule_block(&env, 0, 11);
	if (!err)
		err = seed_service(&env, 1, DEFAULT_DST, 32, 11, 0);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, 11, 0);
	if (!err)
		err = expect_u8("rule_idx", meta.rule_idx, 0);
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

static int test_first_match_sets_rule_idx(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 2,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_udp_frame_ports(&frame, 1234, 53) != 0)
		return -1;

	block.rules[0] = allow_rule(IPPROTO_TCP, 0, UINT16_MAX, 80, 80,
				    RULE_F_ENABLED);
	block.rules[1] = allow_rule(IPPROTO_UDP, 0, UINT16_MAX, 53, 53,
				    RULE_F_ENABLED);

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("rule_idx", meta.rule_idx, 1);
	if (!err)
		err = expect_all_drop_counters_zero(&env);

	env_close(&env);
	return err;
}

static int test_zero_rule_block_default_denies(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 0,
	};
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
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_NOT_ALLOWED, 1);

	env_close(&env);
	return err;
}

static int test_absent_rule_block_default_denies(void)
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
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_NOT_ALLOWED, 1);

	env_close(&env);
	return err;
}

static int test_disabled_rule_skips_to_later_match(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 2,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	block.rules[0] = allow_rule(IPPROTO_UDP, 0, UINT16_MAX, 53, 53, 0);
	block.rules[1] = allow_rule(IPPROTO_UDP, 0, UINT16_MAX, 53, 53,
				    RULE_F_ENABLED);

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("rule_idx", meta.rule_idx, 1);

	env_close(&env);
	return err;
}

static int test_strict_any_rejects_gre(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 1,
	};
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_GRE, 0, 5) != 0)
		return -1;

	block.rules[0] = match_all_rule();

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_NOT_ALLOWED, 1);

	env_close(&env);
	return err;
}

static int test_port_boundaries_match_inclusive_range(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 1,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	block.rules[0] = allow_rule(IPPROTO_TCP, 0, UINT16_MAX, 80, 80,
				    RULE_F_ENABLED);

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);

	if (!err)
		err = build_tcp_frame_ports(&frame, 1234, 79);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval 79", retval, XDP_DROP);

	if (!err)
		err = build_tcp_frame_ports(&frame, 1234, 80);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("rule_idx", meta.rule_idx, 0);

	if (!err)
		err = build_tcp_frame_ports(&frame, 1234, 81);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval 81", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_NOT_ALLOWED, 2);

	env_close(&env);
	return err;
}

static int test_src_range_and_dst_wildcard(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 1,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	block.rules[0] = allow_rule(IPPROTO_UDP, 1000, 2000, 0, UINT16_MAX,
				    RULE_F_ENABLED);

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);

	if (!err)
		err = build_udp_frame_ports(&frame, 1500, 9999);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("rule_idx", meta.rule_idx, 0);

	if (!err)
		err = build_udp_frame_ports(&frame, 999, 9999);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval low sport", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_NOT_ALLOWED, 1);

	env_close(&env);
	return err;
}

static int test_rule_count_clamps_to_sixteen(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 99,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	for (int i = 0; i < RULE_MAX; i++)
		block.rules[i] = allow_rule(IPPROTO_TCP, 0, UINT16_MAX, 80, 80,
					    RULE_F_ENABLED);
	block.rules[15] = allow_rule(IPPROTO_UDP, 0, UINT16_MAX, 53, 53,
				     RULE_F_ENABLED);

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("rule_idx", meta.rule_idx, 15);

	env_close(&env);
	return err;
}

static int test_deterministic_pps_quota_drops_after_budget(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 1,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	block.rules[0] = default_udp_rule();
	block.rules[0].pps = 3;
	block.rules[0].flags |= RULE_F_PPS_SET;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_rl_config(&env, 1);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);

	for (int i = 0; !err && i < 5; i++) {
		err = run_frame_current_maps(&env, &frame, &retval);
		if (!err && i < 3)
			err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID,
						   0);
		if (!err && i < 3)
			err = expect_u8("rule_idx", meta.rule_idx, 0);
		if (!err && i >= 3)
			err = expect_u32("retval", retval, XDP_DROP);
	}
	if (!err)
		err = expect_counter(&env, DR_RATE_LIMIT_DROP, 2);

	env_close(&env);
	return err;
}

static int test_quota_overflow_does_not_fall_through(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 2,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	block.rules[0] = default_udp_rule();
	block.rules[0].pps = 1;
	block.rules[0].flags |= RULE_F_PPS_SET;
	block.rules[1] = match_all_rule();

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_rl_config(&env, 1);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("rule_idx", meta.rule_idx, 0);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_RATE_LIMIT_DROP, 1);

	env_close(&env);
	return err;
}

static int test_no_quota_rule_always_admits(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 1,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	block.rules[0] = default_udp_rule();

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_rl_config(&env, 1);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);

	for (int i = 0; !err && i < 5; i++) {
		err = run_frame_current_maps(&env, &frame, &retval);
		if (!err)
			err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID,
						   0);
	}
	if (!err)
		err = expect_counter(&env, DR_RATE_LIMIT_DROP, 0);

	env_close(&env);
	return err;
}

static int test_pps_zero_blocks(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 1,
	};
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	block.rules[0] = default_udp_rule();
	block.rules[0].flags |= RULE_F_PPS_SET;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_rl_config(&env, 1);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_RATE_LIMIT_DROP, 1);

	env_close(&env);
	return err;
}

static int test_bps_exhausts_by_bytes(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 1,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	block.rules[0] = default_udp_rule();
	block.rules[0].bps = frame.len * 2;
	block.rules[0].flags |= RULE_F_BPS_SET;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_rl_config(&env, 1);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);

	for (int i = 0; !err && i < 3; i++) {
		err = run_frame_current_maps(&env, &frame, &retval);
		if (!err && i < 2)
			err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID,
						   0);
		if (!err && i >= 2)
			err = expect_u32("retval", retval, XDP_DROP);
	}
	if (!err)
		err = expect_counter(&env, DR_RATE_LIMIT_DROP, 1);

	env_close(&env);
	return err;
}

static int test_pps_drop_leaves_bps_tokens_untouched(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 1,
	};
	struct rl_bucket bucket;
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u64 bps_budget;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	bps_budget = frame.len * 10;
	block.rules[0] = default_udp_rule();
	block.rules[0].pps = 1;
	block.rules[0].bps = bps_budget;
	block.rules[0].flags |= RULE_F_PPS_SET | RULE_F_BPS_SET;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_rl_config(&env, 1);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("second retval", retval, XDP_DROP);
	if (!err)
		err = read_bucket_cpu0(&env, DEFAULT_SERVICE_ID, 0, &bucket);
	if (!err)
		err = expect_u32("bps_tokens preserved",
				 (__u32)bucket.bps_tokens,
				 (__u32)(bps_budget - frame.len));

	env_close(&env);
	return err;
}

static int test_reset_on_swap_admits_after_version_change(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 1,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	block.rules[0] = default_udp_rule();
	block.rules[0].pps = 1;
	block.rules[0].flags |= RULE_F_PPS_SET;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_rl_config(&env, 1);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("exhausted retval", retval, XDP_DROP);
	if (!err) {
		block.version = 2;
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	}
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_counter(&env, DR_RATE_LIMIT_DROP, 1);

	env_close(&env);
	return err;
}

static int test_normal_mode_fresh_bucket_admits(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 1,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	block.rules[0] = default_udp_rule();
	block.rules[0].pps = 1;
	block.rules[0].flags |= RULE_F_PPS_SET;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_counter(&env, DR_RATE_LIMIT_DROP, 0);

	env_close(&env);
	return err;
}

static int test_esp_ipv4_drops_not_allowed(void)
{
	struct pkt_frame frame;
	struct test_env env;
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
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_NOT_ALLOWED, 1);

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

static int pin_to_cpu0(void)
{
	cpu_set_t set;

	CPU_ZERO(&set);
	CPU_SET(0, &set);
	if (sched_setaffinity(0, sizeof(set), &set) != 0) {
		fprintf(stderr, "failed to pin test runner to CPU 0: %s\n",
			strerror(errno));
		return -1;
	}

	return 0;
}

int main(void)
{
	const struct test_case tests[] = {
		{ "config maps load", test_config_maps_load },
		{ "drop reason ABI exposes 16 slots",
		  test_drop_reason_abi_exposes_16_slots },
		{ "ringbuf delivers after test_run",
		  test_ringbuf_delivers_after_test_run },
		{ "sampling disabled keeps counters exact",
		  test_sampling_disabled_keeps_counters_exact },
		{ "sampling budget limits events and keeps content",
		  test_sampling_budget_limits_events_and_keeps_content },
		{ "bad reason clamps to map_error",
		  test_bad_reason_clamps_to_map_error },
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
		{ "valid other IPv4 drops not_allowed",
		  test_valid_other_ipv4_drops_not_allowed },
		{ "first match sets rule_idx",
		  test_first_match_sets_rule_idx },
		{ "zero rule block default denies",
		  test_zero_rule_block_default_denies },
		{ "absent rule block default denies",
		  test_absent_rule_block_default_denies },
		{ "disabled rule skips to later match",
		  test_disabled_rule_skips_to_later_match },
		{ "strict any rejects GRE", test_strict_any_rejects_gre },
		{ "port boundaries match inclusive range",
		  test_port_boundaries_match_inclusive_range },
		{ "src range and dst wildcard",
		  test_src_range_and_dst_wildcard },
		{ "rule_count clamps to 16",
		  test_rule_count_clamps_to_sixteen },
		{ "deterministic PPS quota drops after budget",
		  test_deterministic_pps_quota_drops_after_budget },
		{ "quota overflow does not fall through",
		  test_quota_overflow_does_not_fall_through },
		{ "no quota rule always admits",
		  test_no_quota_rule_always_admits },
		{ "PPS zero blocks", test_pps_zero_blocks },
		{ "BPS exhausts by bytes", test_bps_exhausts_by_bytes },
		{ "PPS drop leaves BPS tokens untouched",
		  test_pps_drop_leaves_bps_tokens_untouched },
		{ "reset on swap admits after version change",
		  test_reset_on_swap_admits_after_version_change },
		{ "normal mode fresh bucket admits",
		  test_normal_mode_fresh_bucket_admits },
		{ "ESP IPv4 drops not_allowed",
		  test_esp_ipv4_drops_not_allowed },
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

	if (pin_to_cpu0() != 0)
		return 1;

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
