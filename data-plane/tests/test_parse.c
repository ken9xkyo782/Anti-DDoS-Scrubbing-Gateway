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
#include "fairness.h"
#include "node_control.h"
#include "pkt_build.h"
#include "pkt_meta.h"
#include "rules.h"
#include "service.h"
#include "svc_ratelimit.h"
#include "nexthop.h"
#include "svc_stat.h"
#include "blacklist.h"
#include "whitelist.h"
#include "xdp_gateway.test.skel.h"

/*
 * Pull in the xdpgw-apply helper core (fd-taking, static inline; main() elided)
 * so the fresh-inner composition it depends on is exercised in-process here,
 * against the real skeleton fds — the DBS-09 de-risk (AD-028 testability note).
 */
#define XDPGW_APPLY_NO_MAIN
#define XDPGW_APPLY_TEST
#include "../tools/xdpgw-apply.c"

#define DEFAULT_SERVICE_ID 42
#define DEFAULT_SRC htonl(TEST_SRC_PUB_A)
#define DEFAULT_DST htonl(0x0a000002)
#define FAIR_TEST_B_SERVICE_ID 43
#define FAIR_TEST_B_DST htonl(0x0a000003)

struct test_env {
	struct xdp_gateway_test_bpf *skel;
	int prog_fd;
	int counter_fd;
	int svc_stat_fd;
	int meta_fd;
	int service_inner0_fd;
	int service_inner1_fd;
	int service_map_fd;
	int rule_block0_fd;
	int rule_block1_fd;
	int rule_block_map_fd;
	int global_blacklist_bloom0_fd;
	int global_blacklist_bloom1_fd;
	int global_blacklist_bloom_fd;
	int global_blacklist_lpm0_fd;
	int global_blacklist_lpm1_fd;
	int global_blacklist_lpm_fd;
	int service_blacklist_bloom0_fd;
	int service_blacklist_bloom1_fd;
	int service_blacklist_bloom_fd;
	int service_blacklist_lpm0_fd;
	int service_blacklist_lpm1_fd;
	int service_blacklist_lpm_fd;
	int whitelist_bloom0_fd;
	int whitelist_bloom1_fd;
	int whitelist_bloom_fd;
	int whitelist_lpm0_fd;
	int whitelist_lpm1_fd;
	int whitelist_lpm_fd;
	int blocked_port_bitmap0_fd;
	int blocked_port_bitmap1_fd;
	int blocked_port_bitmap_fd;
	int vip_config0_fd;
	int vip_config1_fd;
	int vip_config_map_fd;
	int vip_ceiling_state_fd;
	int fair_config0_fd;
	int fair_config1_fd;
	int fair_config_map_fd;
	int fair_node_config_fd;
	int svc_committed_state_fd;
	int svc_burst_state_fd;
	int node_burst_state_fd;
	int service_ingress_cap_state_fd;
	int svc_rl_state_fd;
	int svc_rl_config0_fd;
	int svc_rl_config1_fd;
	int svc_rl_config_map_fd;
	int ratelimit_config_fd;
	int active_config_fd;
	int tx_devmap_fd;
	int node_control_fd;
	int bypass_counter_fd;
	int nexthop_map_fd;
	int trigger_fd;
	int ringbuf_fd;
	int sample_config_fd;
	int sample_bucket_fd;
	int sample_stats_fd;
	int gbl_meta_fd;
	int bloom_stats_fd;
	int possible_cpus;
};

static int build_default_udp_frame(struct pkt_frame *frame);
static int set_ipv4_addrs(struct pkt_frame *frame, __u32 src_host,
			  __u32 dst_host);
static int clear_u32_hash_map(int fd);

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
	env->svc_stat_fd = bpf_map__fd(env->skel->maps.svc_stat_map);
	env->meta_fd = bpf_map__fd(env->skel->maps.test_meta_map);
	env->service_inner0_fd = bpf_map__fd(env->skel->maps.service_inner_0);
	env->service_inner1_fd = bpf_map__fd(env->skel->maps.service_inner_1);
	env->service_map_fd = bpf_map__fd(env->skel->maps.service_map);
	env->rule_block0_fd = bpf_map__fd(env->skel->maps.rule_block_0);
	env->rule_block1_fd = bpf_map__fd(env->skel->maps.rule_block_1);
	env->rule_block_map_fd = bpf_map__fd(env->skel->maps.rule_block_map);
	env->global_blacklist_bloom0_fd =
		bpf_map__fd(env->skel->maps.global_blacklist_bloom_0);
	env->global_blacklist_bloom1_fd =
		bpf_map__fd(env->skel->maps.global_blacklist_bloom_1);
	env->global_blacklist_bloom_fd =
		bpf_map__fd(env->skel->maps.global_blacklist_bloom);
	env->global_blacklist_lpm0_fd =
		bpf_map__fd(env->skel->maps.global_blacklist_lpm_0);
	env->global_blacklist_lpm1_fd =
		bpf_map__fd(env->skel->maps.global_blacklist_lpm_1);
	env->global_blacklist_lpm_fd =
		bpf_map__fd(env->skel->maps.global_blacklist_lpm);
	env->service_blacklist_bloom0_fd =
		bpf_map__fd(env->skel->maps.service_blacklist_bloom_0);
	env->service_blacklist_bloom1_fd =
		bpf_map__fd(env->skel->maps.service_blacklist_bloom_1);
	env->service_blacklist_bloom_fd =
		bpf_map__fd(env->skel->maps.service_blacklist_bloom);
	env->service_blacklist_lpm0_fd =
		bpf_map__fd(env->skel->maps.service_blacklist_lpm_0);
	env->service_blacklist_lpm1_fd =
		bpf_map__fd(env->skel->maps.service_blacklist_lpm_1);
	env->service_blacklist_lpm_fd =
		bpf_map__fd(env->skel->maps.service_blacklist_lpm);
	env->whitelist_bloom0_fd = bpf_map__fd(env->skel->maps.whitelist_bloom_0);
	env->whitelist_bloom1_fd = bpf_map__fd(env->skel->maps.whitelist_bloom_1);
	env->whitelist_bloom_fd = bpf_map__fd(env->skel->maps.whitelist_bloom);
	env->whitelist_lpm0_fd = bpf_map__fd(env->skel->maps.whitelist_lpm_0);
	env->whitelist_lpm1_fd = bpf_map__fd(env->skel->maps.whitelist_lpm_1);
	env->whitelist_lpm_fd = bpf_map__fd(env->skel->maps.whitelist_lpm);
	env->blocked_port_bitmap0_fd =
		bpf_map__fd(env->skel->maps.udp_blocked_port_bitmap_0);
	env->blocked_port_bitmap1_fd =
		bpf_map__fd(env->skel->maps.udp_blocked_port_bitmap_1);
	env->blocked_port_bitmap_fd =
		bpf_map__fd(env->skel->maps.udp_blocked_port_bitmap);
	env->vip_config0_fd = bpf_map__fd(env->skel->maps.vip_config_0);
	env->vip_config1_fd = bpf_map__fd(env->skel->maps.vip_config_1);
	env->vip_config_map_fd = bpf_map__fd(env->skel->maps.vip_config_map);
	env->vip_ceiling_state_fd = bpf_map__fd(env->skel->maps.vip_ceiling_state);
	env->fair_config0_fd = bpf_map__fd(env->skel->maps.fair_config_0);
	env->fair_config1_fd = bpf_map__fd(env->skel->maps.fair_config_1);
	env->fair_config_map_fd = bpf_map__fd(env->skel->maps.fair_config_map);
	env->fair_node_config_fd = bpf_map__fd(env->skel->maps.fair_node_config);
	env->svc_committed_state_fd =
		bpf_map__fd(env->skel->maps.svc_committed_state);
	env->svc_burst_state_fd = bpf_map__fd(env->skel->maps.svc_burst_state);
	env->node_burst_state_fd = bpf_map__fd(env->skel->maps.node_burst_state);
	env->service_ingress_cap_state_fd =
		bpf_map__fd(env->skel->maps.service_ingress_cap_state);
	env->svc_rl_state_fd = bpf_map__fd(env->skel->maps.svc_rl_state);
	env->svc_rl_config0_fd = bpf_map__fd(env->skel->maps.svc_rl_config_0);
	env->svc_rl_config1_fd = bpf_map__fd(env->skel->maps.svc_rl_config_1);
	env->svc_rl_config_map_fd = bpf_map__fd(env->skel->maps.svc_rl_config_map);
	env->ratelimit_config_fd = bpf_map__fd(env->skel->maps.ratelimit_config);
	env->active_config_fd = bpf_map__fd(env->skel->maps.active_config);
	env->tx_devmap_fd = bpf_map__fd(env->skel->maps.tx_devmap);
	env->node_control_fd = bpf_map__fd(env->skel->maps.node_control);
	env->bypass_counter_fd = bpf_map__fd(env->skel->maps.bypass_counter);
	env->nexthop_map_fd = bpf_map__fd(env->skel->maps.nexthop_map);
	env->trigger_fd = bpf_map__fd(env->skel->maps.test_trigger_map);
	env->ringbuf_fd = bpf_map__fd(env->skel->maps.drop_ringbuf);
	env->sample_config_fd = bpf_map__fd(env->skel->maps.sample_config);
	env->sample_bucket_fd = bpf_map__fd(env->skel->maps.sample_bucket);
	env->sample_stats_fd = bpf_map__fd(env->skel->maps.sample_stats);
	env->gbl_meta_fd = bpf_map__fd(env->skel->maps.gbl_meta);
	env->bloom_stats_fd = bpf_map__fd(env->skel->maps.bloom_stats);
	if (env->prog_fd < 0 || env->counter_fd < 0 || env->svc_stat_fd < 0 ||
	    env->meta_fd < 0 ||
	    env->service_inner0_fd < 0 || env->service_inner1_fd < 0 ||
	    env->service_map_fd < 0 || env->rule_block0_fd < 0 ||
	    env->rule_block1_fd < 0 || env->rule_block_map_fd < 0 ||
	    env->global_blacklist_bloom0_fd < 0 ||
	    env->global_blacklist_bloom1_fd < 0 ||
	    env->global_blacklist_bloom_fd < 0 ||
	    env->global_blacklist_lpm0_fd < 0 ||
	    env->global_blacklist_lpm1_fd < 0 ||
	    env->global_blacklist_lpm_fd < 0 ||
	    env->service_blacklist_bloom0_fd < 0 ||
	    env->service_blacklist_bloom1_fd < 0 ||
	    env->service_blacklist_bloom_fd < 0 ||
	    env->service_blacklist_lpm0_fd < 0 ||
	    env->service_blacklist_lpm1_fd < 0 ||
	    env->service_blacklist_lpm_fd < 0 ||
	    env->whitelist_bloom0_fd < 0 || env->whitelist_bloom1_fd < 0 ||
	    env->whitelist_bloom_fd < 0 || env->whitelist_lpm0_fd < 0 ||
	    env->whitelist_lpm1_fd < 0 || env->whitelist_lpm_fd < 0 ||
	    env->blocked_port_bitmap0_fd < 0 ||
	    env->blocked_port_bitmap1_fd < 0 ||
	    env->blocked_port_bitmap_fd < 0 ||
	    env->vip_config0_fd < 0 || env->vip_config1_fd < 0 ||
	    env->vip_config_map_fd < 0 || env->vip_ceiling_state_fd < 0 ||
	    env->fair_config0_fd < 0 || env->fair_config1_fd < 0 ||
	    env->fair_config_map_fd < 0 || env->fair_node_config_fd < 0 ||
	    env->svc_committed_state_fd < 0 || env->svc_burst_state_fd < 0 ||
	    env->node_burst_state_fd < 0 ||
	    env->service_ingress_cap_state_fd < 0 ||
	    env->svc_rl_state_fd < 0 || env->svc_rl_config0_fd < 0 ||
	    env->svc_rl_config1_fd < 0 || env->svc_rl_config_map_fd < 0 ||
	    env->ratelimit_config_fd < 0 ||
	    env->active_config_fd < 0 || env->tx_devmap_fd < 0 ||
	    env->node_control_fd < 0 || env->bypass_counter_fd < 0 ||
	    env->nexthop_map_fd < 0 || env->trigger_fd < 0 ||
	    env->ringbuf_fd < 0 || env->sample_config_fd < 0 ||
	    env->sample_bucket_fd < 0 || env->sample_stats_fd < 0 ||
	    env->gbl_meta_fd < 0 || env->bloom_stats_fd < 0) {
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

static int set_node_bypass(struct test_env *env, __u32 bypass)
{
	struct node_control control = {
		.bypass = bypass,
	};
	__u32 key = 0;

	return bpf_map_update_elem(env->node_control_fd, &key, &control,
				   BPF_ANY);
}

static int reset_bypass_counter(struct test_env *env)
{
	struct bypass_stat *zero;
	__u32 key = 0;
	int err;

	zero = calloc(env->possible_cpus, sizeof(*zero));
	if (!zero)
		return -1;

	err = bpf_map_update_elem(env->bypass_counter_fd, &key, zero, BPF_ANY);
	free(zero);
	return err;
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
	if (!err && clear_u32_hash_map(env->svc_stat_fd) != 0)
		err = -1;
	if (!err && set_node_bypass(env, 0) != 0)
		err = -1;
	if (!err && reset_bypass_counter(env) != 0)
		err = -1;

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

	for (key = 0; !err && key < BLOOM_STAT_MAX; key++) {
		if (bpf_map_update_elem(env->bloom_stats_fd, &key, zero_counts, 0) != 0)
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

static int clear_whitelist_lpm_map(int fd)
{
	struct wl_lpm_key key;

	while (bpf_map_get_next_key(fd, NULL, &key) == 0) {
		if (bpf_map_delete_elem(fd, &key) != 0)
			return -1;
	}

	return errno == ENOENT ? 0 : -1;
}

static int clear_global_blacklist_lpm_map(int fd)
{
	struct bl_lpm_key key;

	while (bpf_map_get_next_key(fd, NULL, &key) == 0) {
		if (bpf_map_delete_elem(fd, &key) != 0)
			return -1;
	}

	return errno == ENOENT ? 0 : -1;
}

static int clear_service_blacklist_lpm_map(int fd)
{
	struct sbl_lpm_key key;

	while (bpf_map_get_next_key(fd, NULL, &key) == 0) {
		if (bpf_map_delete_elem(fd, &key) != 0)
			return -1;
	}

	return errno == ENOENT ? 0 : -1;
}

static int reset_gbl_meta_map(int fd)
{
	struct gbl_meta zero = {};

	for (__u32 key = 0; key < SERVICE_SLOTS; key++) {
		if (bpf_map_update_elem(fd, &key, &zero, 0) != 0)
			return -1;
	}

	return 0;
}

static int reset_blocked_port_bitmap_map(int fd)
{
	__u64 zero = 0;

	for (__u32 key = 0; key < BLOCKED_PORT_WORDS; key++) {
		if (bpf_map_update_elem(fd, &key, &zero, 0) != 0)
			return -1;
	}

	return 0;
}

static int reset_fair_node_config_map(int fd)
{
	struct fair_node_config zero = {};

	for (__u32 key = 0; key < SERVICE_SLOTS; key++) {
		if (bpf_map_update_elem(fd, &key, &zero, 0) != 0)
			return -1;
	}

	return 0;
}

static int clear_vip_config_map(int fd)
{
	__u32 key;

	while (bpf_map_get_next_key(fd, NULL, &key) == 0) {
		if (bpf_map_delete_elem(fd, &key) != 0)
			return -1;
	}

	return errno == ENOENT ? 0 : -1;
}

static int clear_vip_ceiling_state(int fd)
{
	__u32 key;

	while (bpf_map_get_next_key(fd, NULL, &key) == 0) {
		if (bpf_map_delete_elem(fd, &key) != 0)
			return -1;
	}

	return errno == ENOENT ? 0 : -1;
}

static int clear_u32_hash_map(int fd)
{
	__u32 key;

	while (bpf_map_get_next_key(fd, NULL, &key) == 0) {
		if (bpf_map_delete_elem(fd, &key) != 0)
			return -1;
	}

	return errno == ENOENT ? 0 : -1;
}

static int reset_node_burst_state(struct test_env *env)
{
	struct rl_bucket *zero;
	__u32 key = 0;
	int err;

	zero = calloc(env->possible_cpus, sizeof(*zero));
	if (!zero)
		return -1;

	err = bpf_map_update_elem(env->node_burst_state_fd, &key, zero, 0);
	free(zero);
	return err;
}

static int reset_rate_limit(struct test_env *env)
{
	struct ratelimit_config config = {};
	__u32 key = 0;

	if (clear_u32_hash_map(env->svc_rl_state_fd) != 0)
		return -1;
	if (clear_u32_hash_map(env->svc_rl_config0_fd) != 0)
		return -1;
	if (clear_u32_hash_map(env->svc_rl_config1_fd) != 0)
		return -1;
	if (clear_vip_ceiling_state(env->vip_ceiling_state_fd) != 0)
		return -1;
	if (clear_u32_hash_map(env->service_ingress_cap_state_fd) != 0)
		return -1;
	if (clear_u32_hash_map(env->svc_committed_state_fd) != 0)
		return -1;
	if (clear_u32_hash_map(env->svc_burst_state_fd) != 0)
		return -1;
	if (reset_node_burst_state(env) != 0)
		return -1;
	return bpf_map_update_elem(env->ratelimit_config_fd, &key, &config, 0);
}

static int reset_config(struct test_env *env)
{
	struct active_config config = {};
	__u32 key = 0;

	if (clear_service_map(env->service_inner0_fd) != 0 ||
	    clear_service_map(env->service_inner1_fd) != 0 ||
	    clear_rule_block_map(env->rule_block0_fd) != 0 ||
	    clear_rule_block_map(env->rule_block1_fd) != 0 ||
	    clear_global_blacklist_lpm_map(env->global_blacklist_lpm0_fd) != 0 ||
	    clear_global_blacklist_lpm_map(env->global_blacklist_lpm1_fd) != 0 ||
	    clear_service_blacklist_lpm_map(env->service_blacklist_lpm0_fd) != 0 ||
	    clear_service_blacklist_lpm_map(env->service_blacklist_lpm1_fd) != 0 ||
	    clear_whitelist_lpm_map(env->whitelist_lpm0_fd) != 0 ||
	    clear_whitelist_lpm_map(env->whitelist_lpm1_fd) != 0 ||
	    clear_u32_hash_map(env->fair_config0_fd) != 0 ||
	    clear_u32_hash_map(env->fair_config1_fd) != 0 ||
	    reset_fair_node_config_map(env->fair_node_config_fd) != 0 ||
	    reset_gbl_meta_map(env->gbl_meta_fd) != 0 ||
	    reset_blocked_port_bitmap_map(env->blocked_port_bitmap0_fd) != 0 ||
	    reset_blocked_port_bitmap_map(env->blocked_port_bitmap1_fd) != 0 ||
	    clear_vip_config_map(env->vip_config0_fd) != 0 ||
	    clear_vip_config_map(env->vip_config1_fd) != 0 ||
	    clear_u32_hash_map(env->nexthop_map_fd) != 0)
		return -1;

	if (bpf_map_update_elem(env->active_config_fd, &key, &config, 0) != 0)
		return -1;

	// Pre-seed resolved next-hops for commonly used test service IDs
	__u32 ids[] = {DEFAULT_SERVICE_ID, FAIR_TEST_B_SERVICE_ID, 11, 77, 99, 4242};
	struct nexthop nh = {
		.dst_mac = {0x00, 0xaa, 0xbb, 0xcc, 0xdd, 0xee},
		.src_mac = {0x00, 0x11, 0x22, 0x33, 0x44, 0x55},
		.resolved = 1,
	};
	for (size_t i = 0; i < sizeof(ids)/sizeof(ids[0]); i++) {
		if (bpf_map_update_elem(env->nexthop_map_fd, &ids[i], &nh, BPF_ANY) != 0)
			return -1;
	}

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

static int fair_config_fd_for_slot(struct test_env *env, __u32 slot)
{
	if (slot == 0)
		return env->fair_config0_fd;
	if (slot == 1)
		return env->fair_config1_fd;

	errno = EINVAL;
	return -1;
}

static int global_blacklist_bloom_fd_for_slot(struct test_env *env, __u32 slot)
{
	if (slot == 0)
		return env->global_blacklist_bloom0_fd;
	if (slot == 1)
		return env->global_blacklist_bloom1_fd;

	errno = EINVAL;
	return -1;
}

static int global_blacklist_lpm_fd_for_slot(struct test_env *env, __u32 slot)
{
	if (slot == 0)
		return env->global_blacklist_lpm0_fd;
	if (slot == 1)
		return env->global_blacklist_lpm1_fd;

	errno = EINVAL;
	return -1;
}

static int service_blacklist_bloom_fd_for_slot(struct test_env *env, __u32 slot)
{
	if (slot == 0)
		return env->service_blacklist_bloom0_fd;
	if (slot == 1)
		return env->service_blacklist_bloom1_fd;

	errno = EINVAL;
	return -1;
}

static int service_blacklist_lpm_fd_for_slot(struct test_env *env, __u32 slot)
{
	if (slot == 0)
		return env->service_blacklist_lpm0_fd;
	if (slot == 1)
		return env->service_blacklist_lpm1_fd;

	errno = EINVAL;
	return -1;
}

static int whitelist_bloom_fd_for_slot(struct test_env *env, __u32 slot)
{
	if (slot == 0)
		return env->whitelist_bloom0_fd;
	if (slot == 1)
		return env->whitelist_bloom1_fd;

	errno = EINVAL;
	return -1;
}

static int whitelist_lpm_fd_for_slot(struct test_env *env, __u32 slot)
{
	if (slot == 0)
		return env->whitelist_lpm0_fd;
	if (slot == 1)
		return env->whitelist_lpm1_fd;

	errno = EINVAL;
	return -1;
}

static int blocked_port_bitmap_fd_for_slot(struct test_env *env, __u32 slot)
{
	if (slot == 0)
		return env->blocked_port_bitmap0_fd;
	if (slot == 1)
		return env->blocked_port_bitmap1_fd;

	errno = EINVAL;
	return -1;
}

static int vip_config_fd_for_slot(struct test_env *env, __u32 slot)
{
	if (slot == 0)
		return env->vip_config0_fd;
	if (slot == 1)
		return env->vip_config1_fd;

	errno = EINVAL;
	return -1;
}

static __u32 ipv4_prefix_mask(__u32 prefixlen)
{
	if (prefixlen == 0)
		return 0;
	return UINT32_MAX << (32 - prefixlen);
}

static struct fair_config fair_default_config(void)
{
	struct fair_config config = {
		.version = 1,
		.committed_bps = FAIR_RATE_MAX,
		.burst_bps = FAIR_RATE_MAX,
		.cap_bps = FAIR_RATE_MAX,
		.cap_pps = FAIR_RATE_MAX,
	};

	return config;
}

static int seed_fair_config(struct test_env *env, __u32 slot,
			    __u32 service_id, const struct fair_config *config)
{
	int fd = fair_config_fd_for_slot(env, slot);

	if (fd < 0)
		return -1;
	return bpf_map_update_elem(fd, &service_id, config, BPF_ANY);
}

static int seed_fair_node_config(struct test_env *env, __u32 slot,
				  const struct fair_node_config *config)
{
	return bpf_map_update_elem(env->fair_node_config_fd, &slot, config,
				  BPF_ANY);
}

static int seed_service_raw(struct test_env *env, __u32 slot, __be32 addr,
			    __u32 prefixlen, __u32 service_id, __u8 enabled,
			    __u8 wl_flags)
{
	struct service_key key = {
		.prefixlen = prefixlen,
		.addr = addr,
	};
	struct service_val val = {
		.service_id = service_id,
		.enabled = enabled,
		.wl_flags = wl_flags,
	};
	int fd = service_fd_for_slot(env, slot);

	if (fd < 0)
		return -1;

	if (bpf_map_update_elem(fd, &key, &val, BPF_ANY) != 0)
		return -1;

	struct nexthop nh = {
		.dst_mac = {0x00, 0xaa, 0xbb, 0xcc, 0xdd, 0xee},
		.src_mac = {0x00, 0x11, 0x22, 0x33, 0x44, 0x55},
		.resolved = 1,
	};
	return bpf_map_update_elem(env->nexthop_map_fd, &service_id, &nh, BPF_ANY);
}

static int seed_service_flags(struct test_env *env, __u32 slot, __be32 addr,
			      __u32 prefixlen, __u32 service_id, __u8 enabled,
			      __u8 wl_flags)
{
	struct fair_config config = fair_default_config();

	if (seed_service_raw(env, slot, addr, prefixlen, service_id, enabled,
			     wl_flags) != 0)
		return -1;
	return seed_fair_config(env, slot, service_id, &config);
}

static int seed_service(struct test_env *env, __u32 slot, __be32 addr,
			__u32 prefixlen, __u32 service_id, __u8 enabled)
{
	return seed_service_flags(env, slot, addr, prefixlen, service_id,
				  enabled, 0);
}

static int set_service_wl_flags(struct test_env *env, __u32 slot,
				__u32 service_id, __u8 wl_flags)
{
	struct service_key key;
	struct service_key next_key;
	struct service_key *prev = NULL;
	struct service_val val;
	int fd = service_fd_for_slot(env, slot);

	if (fd < 0)
		return -1;

	while (bpf_map_get_next_key(fd, prev, &next_key) == 0) {
		key = next_key;
		prev = &key;
		if (bpf_map_lookup_elem(fd, &key, &val) != 0)
			return -1;
		if (val.service_id != service_id)
			continue;

		val.wl_flags = wl_flags;
		return bpf_map_update_elem(fd, &key, &val, BPF_ANY);
	}

	errno = ENOENT;
	return -1;
}

static int set_service_bl_flags(struct test_env *env, __u32 slot,
				__u32 service_id, __u8 bl_flags)
{
	struct service_key key;
	struct service_key next_key;
	struct service_key *prev = NULL;
	struct service_val val;
	int fd = service_fd_for_slot(env, slot);

	if (fd < 0)
		return -1;

	while (bpf_map_get_next_key(fd, prev, &next_key) == 0) {
		key = next_key;
		prev = &key;
		if (bpf_map_lookup_elem(fd, &key, &val) != 0)
			return -1;
		if (val.service_id != service_id)
			continue;

		val.bl_flags = bl_flags;
		return bpf_map_update_elem(fd, &key, &val, BPF_ANY);
	}

	errno = ENOENT;
	return -1;
}

static int set_gbl_meta_flags(struct test_env *env, __u32 slot, __u8 flags)
{
	struct gbl_meta meta = {
		.flags = flags,
	};

	return bpf_map_update_elem(env->gbl_meta_fd, &slot, &meta, 0);
}

static int seed_global_blacklist_bloom_key(struct test_env *env, __u32 slot,
					   __u32 src_host)
{
	__be32 key = htonl(src_host & BL_SRC24_MASK);
	int fd = global_blacklist_bloom_fd_for_slot(env, slot);

	if (fd < 0)
		return -1;

	return bpf_map_update_elem(fd, NULL, &key, BPF_ANY);
}

static int seed_global_blacklist_lpm_entry(struct test_env *env, __u32 slot,
					   __u32 cidr_host,
					   __u32 prefixlen)
{
	__u8 present = 1;
	struct bl_lpm_key key = {
		.prefixlen = prefixlen,
		.src = htonl(cidr_host & ipv4_prefix_mask(prefixlen)),
	};
	int fd = global_blacklist_lpm_fd_for_slot(env, slot);

	if (fd < 0)
		return -1;

	return bpf_map_update_elem(fd, &key, &present, BPF_ANY);
}

static int seed_global_blacklist(struct test_env *env, __u32 slot,
				 __u32 cidr_host, __u32 prefixlen)
{
	__u8 flags = GBL_F_ACTIVE;

	if (prefixlen >= GBL_BLOOM_PREFIX &&
	    seed_global_blacklist_bloom_key(env, slot, cidr_host) != 0)
		return -1;
	if (prefixlen < GBL_BLOOM_PREFIX)
		flags |= GBL_F_HAS_BROAD;
	if (seed_global_blacklist_lpm_entry(env, slot, cidr_host,
					    prefixlen) != 0)
		return -1;
	return set_gbl_meta_flags(env, slot, flags);
}

static int seed_service_blacklist_bloom_key(struct test_env *env, __u32 slot,
					    __u32 service_id,
					    __u32 src_host)
{
	struct sbl_bloom_key key = {
		.service_id = htonl(service_id),
		.src24 = htonl(src_host & BL_SRC24_MASK),
	};
	int fd = service_blacklist_bloom_fd_for_slot(env, slot);

	if (fd < 0)
		return -1;

	return bpf_map_update_elem(fd, NULL, &key, BPF_ANY);
}

static int seed_service_blacklist_lpm_entry(struct test_env *env, __u32 slot,
					    __u32 service_id,
					    __u32 cidr_host,
					    __u32 prefixlen)
{
	__u8 present = 1;
	struct sbl_lpm_key key = {
		.prefixlen = 32 + prefixlen,
		.service_id = htonl(service_id),
		.src = htonl(cidr_host & ipv4_prefix_mask(prefixlen)),
	};
	int fd = service_blacklist_lpm_fd_for_slot(env, slot);

	if (fd < 0)
		return -1;

	return bpf_map_update_elem(fd, &key, &present, BPF_ANY);
}

static int seed_service_blacklist(struct test_env *env, __u32 slot,
				  __u32 service_id, __u32 cidr_host,
				  __u32 prefixlen)
{
	__u8 flags = BL_F_ACTIVE;

	if (prefixlen >= SBL_BLOOM_PREFIX &&
	    seed_service_blacklist_bloom_key(env, slot, service_id,
					     cidr_host) != 0)
		return -1;
	if (prefixlen < SBL_BLOOM_PREFIX)
		flags |= BL_F_HAS_BROAD;
	if (seed_service_blacklist_lpm_entry(env, slot, service_id,
					     cidr_host, prefixlen) != 0)
		return -1;
	return set_service_bl_flags(env, slot, service_id, flags);
}

static int seed_whitelist_bloom_key(struct test_env *env, __u32 slot,
				    __u32 service_id, __u32 src_host)
{
	struct wl_bloom_key key = {
		.service_id = htonl(service_id),
		.src24 = htonl(src_host & WL_SRC24_MASK),
	};
	int fd = whitelist_bloom_fd_for_slot(env, slot);

	if (fd < 0)
		return -1;

	return bpf_map_update_elem(fd, NULL, &key, BPF_ANY);
}

static int seed_whitelist_lpm_entry(struct test_env *env, __u32 slot,
				    __u32 service_id, __u32 cidr_host,
				    __u32 prefixlen)
{
	__u8 present = 1;
	struct wl_lpm_key key = {
		.prefixlen = 32 + prefixlen,
		.service_id = htonl(service_id),
		.src = htonl(cidr_host & ipv4_prefix_mask(prefixlen)),
	};
	int fd = whitelist_lpm_fd_for_slot(env, slot);

	if (fd < 0)
		return -1;

	return bpf_map_update_elem(fd, &key, &present, BPF_ANY);
}

static int seed_vip_config(struct test_env *env, __u32 slot, __u32 service_id,
			   const struct vip_config *config)
{
	int fd = vip_config_fd_for_slot(env, slot);

	if (fd < 0)
		return -1;

	return bpf_map_update_elem(fd, &service_id, config, BPF_ANY);
}

static int seed_svc_rl_config(struct test_env *env, __u32 slot, __u32 service_id,
			      const struct svc_rl_config *config)
{
	int fd = slot == 0 ? env->svc_rl_config0_fd : env->svc_rl_config1_fd;

	if (fd < 0)
		return -1;

	return bpf_map_update_elem(fd, &service_id, config, BPF_ANY);
}

static struct vip_config vip_pps_config(__u64 pps)
{
	struct vip_config config = {
		.version = 1,
		.flags = VIP_F_PPS_SET,
		.pps = pps,
	};

	return config;
}

static int seed_whitelist(struct test_env *env, __u32 slot, __u32 service_id,
			  __u32 cidr_host, __u32 prefixlen,
			  const struct vip_config *config)
{
	__u8 flags = WL_F_ACTIVE;

	if (prefixlen >= WL_BLOOM_PREFIX &&
	    seed_whitelist_bloom_key(env, slot, service_id, cidr_host) != 0)
		return -1;
	if (prefixlen < WL_BLOOM_PREFIX)
		flags |= WL_F_HAS_BROAD;
	if (seed_whitelist_lpm_entry(env, slot, service_id, cidr_host,
				     prefixlen) != 0)
		return -1;
	if (config && seed_vip_config(env, slot, service_id, config) != 0)
		return -1;

	return set_service_wl_flags(env, slot, service_id, flags);
}

static int seed_blocked_port(struct test_env *env, __u32 slot, __u16 port)
{
	__u32 key = (__u32)port >> 6;
	__u64 bit = 1ULL << ((__u32)port & 63);
	__u64 word = 0;
	int fd = blocked_port_bitmap_fd_for_slot(env, slot);

	if (fd < 0)
		return -1;
	if (bpf_map_lookup_elem(fd, &key, &word) != 0)
		return -1;

	word |= bit;
	return bpf_map_update_elem(fd, &key, &word, 0);
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
	struct ratelimit_config config = {
		.test_no_refill = test_no_refill,
	};
	__u32 key = 0;

	return bpf_map_update_elem(env->ratelimit_config_fd, &key, &config, 0);
}

static int set_test_trigger(struct test_env *env, __u32 value)
{
	__u32 key = 0;

	return bpf_map_update_elem(env->trigger_fd, &key, &value, 0);
}

static struct wl_bloom_key test_wl_bloom_key(__u32 src24)
{
	struct wl_bloom_key key = {
		.service_id = htonl(WL_TEST_BLOOM_SERVICE_ID),
		.src24 = htonl(src24),
	};

	return key;
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

static int read_svc_stat(struct test_env *env, __u32 dp_id,
			 struct svc_stat *sum)
{
	struct svc_stat *values;
	int err;

	values = calloc(env->possible_cpus, sizeof(*values));
	if (!values)
		return -1;

	err = bpf_map_lookup_elem(env->svc_stat_fd, &dp_id, values);
	if (err == 0) {
		memset(sum, 0, sizeof(*sum));
		for (int cpu = 0; cpu < env->possible_cpus; cpu++) {
			sum->clean_pkts += values[cpu].clean_pkts;
			sum->clean_bytes += values[cpu].clean_bytes;
			sum->drop_pkts += values[cpu].drop_pkts;
			sum->drop_bytes += values[cpu].drop_bytes;
			for (int reason = 0; reason < DROP_REASON_CAP; reason++)
				sum->drop_by_reason[reason] +=
					values[cpu].drop_by_reason[reason];
		}
	}

	free(values);
	return err;
}

static int read_bypass_counter(struct test_env *env, struct bypass_stat *sum)
{
	struct bypass_stat *values;
	__u32 key = 0;
	int err;

	values = calloc(env->possible_cpus, sizeof(*values));
	if (!values)
		return -1;

	err = bpf_map_lookup_elem(env->bypass_counter_fd, &key, values);
	if (err == 0) {
		memset(sum, 0, sizeof(*sum));
		for (int cpu = 0; cpu < env->possible_cpus; cpu++) {
			sum->pkts += values[cpu].pkts;
			sum->bytes += values[cpu].bytes;
		}
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

static int read_bloom_stat(struct test_env *env, enum bloom_fp_stage stage,
			   __u64 *sum)
{
	__u64 *values;
	__u32 key = (__u32)stage;
	int err;

	values = calloc(env->possible_cpus, sizeof(*values));
	if (!values)
		return -1;

	err = bpf_map_lookup_elem(env->bloom_stats_fd, &key, values);
	if (err == 0) {
		*sum = 0;
		for (int i = 0; i < env->possible_cpus; i++)
			*sum += values[i];
	}

	free(values);
	return err;
}

static int read_svc_rl_bucket_cpu0(struct test_env *env, __u32 service_id,
				   struct rl_bucket *bucket)
{
	struct rl_bucket *values;
	__u32 key = service_id;
	int err;

	values = calloc(env->possible_cpus, sizeof(*values));
	if (!values)
		return -1;

	err = bpf_map_lookup_elem(env->svc_rl_state_fd, &key, values);
	if (err == 0)
		*bucket = values[0];

	free(values);
	return err;
}

static int read_vip_bucket_cpu0(struct test_env *env, __u32 service_id,
				struct rl_bucket *bucket)
{
	struct rl_bucket *values;
	__u32 key = service_id;
	int err;

	values = calloc(env->possible_cpus, sizeof(*values));
	if (!values)
		return -1;

	err = bpf_map_lookup_elem(env->vip_ceiling_state_fd, &key, values);
	if (err == 0)
		*bucket = values[0];

	free(values);
	return err;
}

static int read_fair_cap_bucket_cpu0(struct test_env *env, __u32 service_id,
				     struct rl_bucket *bucket)
{
	struct rl_bucket *values;
	__u32 key = service_id;
	int err;

	values = calloc(env->possible_cpus, sizeof(*values));
	if (!values)
		return -1;

	err = bpf_map_lookup_elem(env->service_ingress_cap_state_fd, &key,
				  values);
	if (err == 0)
		*bucket = values[0];

	free(values);
	return err;
}

static int read_fair_burst_bucket_cpu0(struct test_env *env, __u32 service_id,
				       struct rl_bucket *bucket)
{
	struct rl_bucket *values;
	__u32 key = service_id;
	int err;

	values = calloc(env->possible_cpus, sizeof(*values));
	if (!values)
		return -1;

	err = bpf_map_lookup_elem(env->svc_burst_state_fd, &key, values);
	if (err == 0)
		*bucket = values[0];

	free(values);
	return err;
}

static int read_fair_node_bucket_cpu0(struct test_env *env,
				      struct rl_bucket *bucket)
{
	struct rl_bucket *values;
	__u32 key = 0;
	int err;

	values = calloc(env->possible_cpus, sizeof(*values));
	if (!values)
		return -1;

	err = bpf_map_lookup_elem(env->node_burst_state_fd, &key, values);
	if (err == 0)
		*bucket = values[0];

	free(values);
	return err;
}

static int read_fair_committed_bucket(struct test_env *env, __u32 service_id,
				      struct fair_committed_bucket *bucket)
{
	return bpf_map_lookup_elem(env->svc_committed_state_fd, &service_id,
				  bucket);
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

static int seed_default_enabled_service(struct test_env *env)
{
	if (seed_service(env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1) != 0)
		return -1;
	if (seed_match_all_rule_block(env, 0, DEFAULT_SERVICE_ID) != 0)
		return -1;
	return set_active(env, 0, 1);
}

static int run_enabled_service_frame(struct test_env *env,
				     const struct pkt_frame *frame,
				     __u32 *retval)
{
	if (reset_maps(env) != 0 || seed_default_enabled_service(env) != 0)
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

static int expect_u64(const char *label, __u64 got, __u64 want)
{
	if (got == want)
		return 0;

	fprintf(stderr, "%s: got %llu, want %llu\n", label,
		(unsigned long long)got, (unsigned long long)want);
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

static int expect_bl_state(struct test_env *env, __u8 want)
{
	struct pkt_meta meta;

	if (read_meta(env, &meta) != 0)
		return -1;
	return expect_u8("bl_state", meta.bl_state, want);
}

static int expect_fd(const char *label, int fd)
{
	if (fd >= 0)
		return 0;

	fprintf(stderr, "%s: invalid fd %d\n", label, fd);
	return -1;
}

static int expect_svc_stats_empty(struct test_env *env)
{
	__u32 dp_id;

	if (bpf_map_get_next_key(env->svc_stat_fd, NULL, &dp_id) != 0 &&
	    errno == ENOENT)
		return 0;

	fprintf(stderr, "svc_stat: unexpectedly contains dp_id %u\n", dp_id);
	return -1;
}

static int expect_svc_stat(struct test_env *env, __u32 dp_id,
			   __u64 clean_pkts, __u64 clean_bytes,
			   __u64 drop_pkts, __u64 drop_bytes,
			   enum drop_reason reason, __u64 reason_count)
{
	struct svc_stat stat;

	if (read_svc_stat(env, dp_id, &stat) != 0) {
		fprintf(stderr, "svc_stat[%u]: read failed: %s\n", dp_id,
			strerror(errno));
		return -1;
	}
	if (expect_u64("svc clean_pkts", stat.clean_pkts, clean_pkts) != 0 ||
	    expect_u64("svc clean_bytes", stat.clean_bytes, clean_bytes) != 0 ||
	    expect_u64("svc drop_pkts", stat.drop_pkts, drop_pkts) != 0 ||
	    expect_u64("svc drop_bytes", stat.drop_bytes, drop_bytes) != 0)
		return -1;
	return expect_u64("svc drop_by_reason", stat.drop_by_reason[reason],
			  reason_count);
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

static int expect_bypass_counter(struct test_env *env, __u64 pkts,
				 __u64 bytes)
{
	struct bypass_stat stat;

	if (read_bypass_counter(env, &stat) != 0) {
		fprintf(stderr, "bypass_counter: read failed: %s\n", strerror(errno));
		return -1;
	}
	if (expect_u64("bypass pkts", stat.pkts, pkts) != 0)
		return -1;
	return expect_u64("bypass bytes", stat.bytes, bytes);
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

static int expect_bloom_stat(struct test_env *env, enum bloom_fp_stage stage,
			     __u64 want)
{
	__u64 got = 0;

	if (read_bloom_stat(env, stage, &got) != 0) {
		fprintf(stderr, "bloom_stat[%u]: read failed: %s\n", stage,
			strerror(errno));
		return -1;
	}

	if (got == want)
		return 0;

	fprintf(stderr, "bloom_stat[%u]: got %llu, want %llu\n", stage,
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
	if (expect_u32("event.src_ip", event->src_ip, DEFAULT_SRC) != 0)
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
		err = set_test_trigger(&env, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MAP_ERROR, 1);

	env_close(&env);
	return err;
}

static int test_fair_committed_spin_lock_mutates_tokens(void)
{
	struct fair_committed_bucket bucket = {};
	struct pkt_frame frame;
	struct test_env env;
	__u32 key = FAIR_TEST_LOCK_SERVICE_ID;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = bpf_map_update_elem(env.svc_committed_state_fd, &key, &bucket,
					  BPF_ANY);
	if (!err)
		err = set_test_trigger(&env, FAIR_TEST_TRIGGER_SPIN_LOCK);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_PASS);
	if (!err &&
	    bpf_map_lookup_elem(env.svc_committed_state_fd, &key, &bucket) != 0) {
		fprintf(stderr, "failed to read committed bucket: %s\n",
			strerror(errno));
		err = -1;
	}
	if (!err)
		err = expect_u64("committed lock tokens", bucket.tokens, 1);

	env_close(&env);
	return err;
}

static struct fair_config fair_cap_config(__u32 version, __u64 pps,
					  __u64 bps)
{
	struct fair_config config = {
		.version = version,
		.committed_bps = FAIR_RATE_MAX,
		.burst_bps = FAIR_RATE_MAX,
		.cap_pps = pps,
		.cap_bps = bps,
	};

	return config;
}

static int setup_fair_cap_service(struct test_env *env,
				  const struct fair_config *config)
{
	if (reset_maps(env) != 0 || set_rl_config(env, 1) != 0 ||
	    seed_service(env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1) != 0 ||
	    seed_fair_config(env, 0, DEFAULT_SERVICE_ID, config) != 0 ||
	    seed_match_all_rule_block(env, 0, DEFAULT_SERVICE_ID) != 0)
		return -1;
	return set_active(env, 0, 1);
}

static int test_ingress_cap_under_cap_continues(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	struct fair_config config;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	config = fair_cap_config(1, 1, frame.len);

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fair_cap_service(&env, &config);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("fair_state", meta.fair_state, FAIR_COMMITTED);
	if (!err)
		err = expect_counter(&env, DR_INGRESS_CAP_DROP, 0);

	env_close(&env);
	return err;
}

static int test_ingress_cap_pps_exhausts_independently(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	struct rl_bucket bucket;
	struct fair_config config;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	config = fair_cap_config(1, 2, frame.len * 10);

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fair_cap_service(&env, &config);
	for (int i = 0; !err && i < 3; i++) {
		err = run_frame_current_maps(&env, &frame, &retval);
		if (!err && i < 2)
			err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
		if (!err && i == 2)
			err = expect_u32("pps cap retval", retval, XDP_DROP);
	}
	if (!err)
		err = read_meta(&env, &meta);
	if (!err)
		err = expect_u8("pps cap fair_state", meta.fair_state,
				FAIR_CAP_DROP);
	if (!err)
		err = read_fair_cap_bucket_cpu0(&env, DEFAULT_SERVICE_ID, &bucket);
	if (!err)
		err = expect_u64("pps cap bps tokens", bucket.bps_tokens,
				frame.len * 8);
	if (!err)
		err = expect_counter(&env, DR_INGRESS_CAP_DROP, 1);

	env_close(&env);
	return err;
}

static int test_ingress_cap_bps_exhausts_independently(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	struct rl_bucket bucket;
	struct fair_config config;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	config = fair_cap_config(1, 10, frame.len * 2);

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fair_cap_service(&env, &config);
	for (int i = 0; !err && i < 3; i++) {
		err = run_frame_current_maps(&env, &frame, &retval);
		if (!err && i < 2)
			err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
		if (!err && i == 2)
			err = expect_u32("bps cap retval", retval, XDP_DROP);
	}
	if (!err)
		err = read_fair_cap_bucket_cpu0(&env, DEFAULT_SERVICE_ID, &bucket);
	if (!err)
		err = expect_u64("bps cap pps tokens", bucket.pps_tokens, 8);
	if (!err)
		err = expect_counter(&env, DR_INGRESS_CAP_DROP, 1);

	env_close(&env);
	return err;
}

static int test_ingress_cap_stops_before_policy_stages(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	struct fair_config config = fair_cap_config(1, 0, 0);
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fair_cap_service(&env, &config);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("stage cap retval", retval, XDP_DROP);
	if (!err)
		err = read_meta(&env, &meta);
	if (!err)
		err = expect_u8("stage wl_state", meta.wl_state, WL_STATE_NONE);
	if (!err)
		err = expect_u8("stage bl_state", meta.bl_state, BL_STATE_NONE);
	if (!err)
		err = expect_u8("stage rule_idx", meta.rule_idx, RULE_IDX_NONE);
	if (!err)
		err = expect_u8("stage fair_state", meta.fair_state,
				FAIR_CAP_DROP);
	if (!err)
		err = expect_counter(&env, DR_INGRESS_CAP_DROP, 1);

	env_close(&env);
	return err;
}

static int test_ingress_cap_precedes_vip(void)
{
	struct vip_config vip = {
		.version = 1,
		.flags = VIP_F_PPS_SET,
		.pps = 10,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	struct fair_config config;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;
	config = fair_cap_config(1, 1, frame.len * 2);

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fair_cap_service(&env, &config);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID,
				     TEST_SRC_PUB_A_NET24, 24, &vip);
	for (int i = 0; !err && i < 2; i++) {
		err = run_frame_current_maps(&env, &frame, &retval);
		if (!err && i == 0)
			err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
		if (!err && i == 0)
			err = expect_u8("VIP wl_state", meta.wl_state,
					WL_STATE_HIT_ADMIT);
		if (!err && i == 1)
			err = expect_u32("VIP cap retval", retval, XDP_DROP);
	}
	if (!err)
		err = read_meta(&env, &meta);
	if (!err)
		err = expect_u8("VIP cap wl_state", meta.wl_state, WL_STATE_NONE);
	if (!err)
		err = expect_u8("VIP cap fair_state", meta.fair_state,
				FAIR_CAP_DROP);
	if (!err)
		err = expect_counter(&env, DR_INGRESS_CAP_DROP, 1);

	env_close(&env);
	return err;
}

static int test_ingress_cap_missing_config_fails_closed(void)
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
		err = seed_service_raw(&env, 0, DEFAULT_DST, 32,
				       DEFAULT_SERVICE_ID, 1, 0);
	if (!err)
		err = seed_match_all_rule_block(&env, 0, DEFAULT_SERVICE_ID);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("missing cap config retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MAP_ERROR, 1);

	env_close(&env);
	return err;
}

static int test_ingress_cap_version_flip_resets_bucket(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	struct fair_config config;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	config = fair_cap_config(1, 1, frame.len * 2);

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fair_cap_service(&env, &config);
	for (int i = 0; !err && i < 2; i++) {
		err = run_frame_current_maps(&env, &frame, &retval);
		if (!err && i == 0)
			err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
		if (!err && i == 1)
			err = expect_u32("pre-flip cap retval", retval, XDP_DROP);
	}
	config.version = 2;
	if (!err)
		err = seed_fair_config(&env, 0, DEFAULT_SERVICE_ID, &config);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_counter(&env, DR_INGRESS_CAP_DROP, 1);

	env_close(&env);
	return err;
}

static struct fair_config fair_ladder_config(__u32 version, __u64 committed_bps,
					       __u64 burst_bps)
{
	struct fair_config config = {
		.version = version,
		.committed_bps = committed_bps,
		.burst_bps = burst_bps,
		.cap_pps = FAIR_RATE_MAX,
		.cap_bps = FAIR_RATE_MAX,
	};

	return config;
}

static int setup_fair_ladder_service(struct test_env *env,
				     const struct fair_config *config,
				     const struct fair_node_config *node)
{
	if (reset_maps(env) != 0 || set_rl_config(env, 1) != 0 ||
	    seed_service(env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1) != 0 ||
	    seed_fair_config(env, 0, DEFAULT_SERVICE_ID, config) != 0 ||
	    seed_fair_node_config(env, 0, node) != 0 ||
	    seed_match_all_rule_block(env, 0, DEFAULT_SERVICE_ID) != 0)
		return -1;
	return set_active(env, 0, 1);
}

static int test_fair_committed_exact_admit_count(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct fair_committed_bucket bucket;
	struct fair_config config;
	struct fair_node_config node = {
		.version = 1,
		.headroom_bps = 0,
	};
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	config = fair_ladder_config(1, frame.len * 2, 0);

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fair_ladder_service(&env, &config, &node);
	for (int i = 0; !err && i < 3; i++) {
		err = run_frame_current_maps(&env, &frame, &retval);
		if (!err && i < 2)
			err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
		if (!err && i < 2)
			err = expect_u8("committed fair_state", meta.fair_state,
					FAIR_COMMITTED);
		if (!err && i == 2)
			err = expect_u32("committed overflow retval", retval, XDP_DROP);
	}
	if (!err)
		err = read_meta(&env, &meta);
	if (!err)
		err = expect_u8("committed overflow fair_state", meta.fair_state,
				FAIR_CEILING_DROP);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_CEILING_DROP, 1);
	if (!err)
		err = read_fair_committed_bucket(&env, DEFAULT_SERVICE_ID, &bucket);
	if (!err)
		err = expect_u64("committed tokens", bucket.tokens, 0);

	env_close(&env);
	return err;
}

static int test_fair_burst_dual_draws_node_headroom(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct rl_bucket service_bucket;
	struct rl_bucket node_bucket;
	struct fair_config config;
	struct fair_node_config node = {
		.version = 1,
	};
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	config = fair_ladder_config(1, 0, frame.len * 2);
	node.headroom_bps = frame.len * 2;

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fair_ladder_service(&env, &config, &node);
	for (int i = 0; !err && i < 2; i++) {
		err = run_frame_current_maps(&env, &frame, &retval);
		if (!err)
			err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
		if (!err)
			err = expect_u8("burst fair_state", meta.fair_state,
					FAIR_BURST);
	}
	if (!err)
		err = read_fair_burst_bucket_cpu0(&env, DEFAULT_SERVICE_ID,
						&service_bucket);
	if (!err)
		err = expect_u64("service burst tokens", service_bucket.bps_tokens, 0);
	if (!err)
		err = read_fair_node_bucket_cpu0(&env, &node_bucket);
	if (!err)
		err = expect_u64("node burst tokens", node_bucket.bps_tokens, 0);

	env_close(&env);
	return err;
}

static int test_fair_service_ceiling_drop(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct fair_config config;
	struct fair_node_config node = {
		.version = 1,
	};
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	config = fair_ladder_config(1, 0, frame.len);
	node.headroom_bps = frame.len * 2;

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fair_ladder_service(&env, &config, &node);
	err = !err ? run_frame_current_maps(&env, &frame, &retval) : err;
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("ceiling retval", retval, XDP_DROP);
	if (!err)
		err = read_meta(&env, &meta);
	if (!err)
		err = expect_u8("ceiling fair_state", meta.fair_state,
				FAIR_CEILING_DROP);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_CEILING_DROP, 1);

	env_close(&env);
	return err;
}

static int test_fair_congestion_drop_keeps_reason_at_node(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct rl_bucket service_bucket;
	struct fair_config config;
	struct fair_node_config node = {
		.version = 1,
		.headroom_bps = 0,
	};
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	config = fair_ladder_config(1, 0, frame.len * 2);

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fair_ladder_service(&env, &config, &node);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("congestion retval", retval, XDP_DROP);
	if (!err)
		err = read_meta(&env, &meta);
	if (!err)
		err = expect_u8("congestion fair_state", meta.fair_state,
				FAIR_CONGESTION_DROP);
	if (!err)
		err = expect_counter(&env, DR_CONGESTION_DROP, 1);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_CEILING_DROP, 0);
	if (!err)
		err = read_fair_burst_bucket_cpu0(&env, DEFAULT_SERVICE_ID,
						&service_bucket);
	if (!err)
		err = expect_u64("unrefunded service burst", service_bucket.bps_tokens,
				frame.len);

	env_close(&env);
	return err;
}

static int test_fair_zero_committed_uses_burst_only(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct fair_committed_bucket committed;
	struct fair_config config;
	struct fair_node_config node = {
		.version = 1,
	};
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	config = fair_ladder_config(1, 0, frame.len);
	node.headroom_bps = frame.len;

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fair_ladder_service(&env, &config, &node);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("best effort fair_state", meta.fair_state, FAIR_BURST);
	if (!err)
		err = read_fair_committed_bucket(&env, DEFAULT_SERVICE_ID, &committed);
	if (!err)
		err = expect_u64("best effort committed tokens", committed.tokens, 0);

	env_close(&env);
	return err;
}

static int test_fair_committed_equals_ceiling_has_no_burst(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct fair_config config;
	struct fair_node_config node = {
		.version = 1,
	};
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	config = fair_ladder_config(1, frame.len, 0);
	node.headroom_bps = frame.len * 2;

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fair_ladder_service(&env, &config, &node);
	err = !err ? run_frame_current_maps(&env, &frame, &retval) : err;
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("equal fair_state", meta.fair_state, FAIR_COMMITTED);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("equal overflow retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_CEILING_DROP, 1);

	env_close(&env);
	return err;
}

static int test_fair_version_flip_regrants_burst_once(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct fair_config config;
	struct fair_node_config node = {
		.version = 1,
	};
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	config = fair_ladder_config(1, 0, frame.len);
	node.headroom_bps = frame.len;

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fair_ladder_service(&env, &config, &node);
	err = !err ? run_frame_current_maps(&env, &frame, &retval) : err;
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("pre-flip ladder retval", retval, XDP_DROP);
	config.version = 2;
	node.version = 2;
	if (!err)
		err = seed_fair_config(&env, 0, DEFAULT_SERVICE_ID, &config);
	if (!err)
		err = seed_fair_node_config(&env, 0, &node);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("post-flip fair_state", meta.fair_state, FAIR_BURST);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_CEILING_DROP, 1);

	env_close(&env);
	return err;
}

static int test_fair_zero_node_headroom_sheds_all_burst(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct fair_config config;
	struct fair_node_config node = {
		.version = 1,
		.headroom_bps = 0,
	};
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	config = fair_ladder_config(1, 0, frame.len * 2);

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fair_ladder_service(&env, &config, &node);
	for (int i = 0; !err && i < 2; i++) {
		err = run_frame_current_maps(&env, &frame, &retval);
		if (!err)
			err = expect_u32("zero headroom retval", retval, XDP_DROP);
	}
	if (!err)
		err = expect_counter(&env, DR_CONGESTION_DROP, 2);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_CEILING_DROP, 0);

	env_close(&env);
	return err;
}

static int setup_fairness_pair(struct test_env *env,
				       const struct fair_config *a_config,
				       const struct fair_config *b_config,
				       const struct fair_node_config *node)
{
	if (reset_maps(env) != 0 || set_rl_config(env, 1) != 0 ||
	    seed_service(env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1) != 0 ||
	    seed_service(env, 0, FAIR_TEST_B_DST, 32, FAIR_TEST_B_SERVICE_ID, 1) != 0 ||
	    seed_fair_config(env, 0, DEFAULT_SERVICE_ID, a_config) != 0 ||
	    seed_fair_config(env, 0, FAIR_TEST_B_SERVICE_ID, b_config) != 0 ||
	    seed_fair_node_config(env, 0, node) != 0 ||
	    seed_match_all_rule_block(env, 0, DEFAULT_SERVICE_ID) != 0 ||
	    seed_match_all_rule_block(env, 0, FAIR_TEST_B_SERVICE_ID) != 0)
		return -1;
	return set_active(env, 0, 1);
}

static int run_fairness_b_committed(struct test_env *env,
					    const struct pkt_frame *frame,
					    __u64 *admitted)
{
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	err = run_frame_current_maps(env, frame, &retval);
	if (!err)
		err = expect_redirect_meta(env, &meta, FAIR_TEST_B_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("B fair_state", meta.fair_state, FAIR_COMMITTED);
	if (!err)
		(*admitted)++;
	return err;
}

static int test_fairness_cap_isolates_committed_neighbor(void)
{
	struct pkt_frame a_frame;
	struct pkt_frame b_frame;
	struct test_env env;
	struct fair_config a_config;
	struct fair_config b_config;
	struct fair_node_config node = {
		.version = 1,
		.headroom_bps = 0,
	};
	struct pkt_meta meta;
	__u64 flooded_b = 0;
	__u64 clear_b = 0;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&a_frame) != 0 ||
	    build_default_udp_frame(&b_frame) != 0 ||
	    set_ipv4_addrs(&b_frame, TEST_SRC_PUB_B, 0x0a000003) != 0)
		return -1;
	a_config = fair_ladder_config(1, a_frame.len, 0);
	a_config.cap_pps = 1;
	a_config.cap_bps = a_frame.len * 2;
	b_config = fair_ladder_config(1, b_frame.len * 4, 0);

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fairness_pair(&env, &a_config, &b_config, &node);
	for (int i = 0; !err && i < 2; i++) {
		err = run_frame_current_maps(&env, &a_frame, &retval);
		if (!err && i == 0)
			err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
		if (!err && i == 1)
			err = expect_u32("cap flood retval", retval, XDP_DROP);
		if (!err && i == 1)
			err = read_meta(&env, &meta);
		if (!err && i == 1)
			err = expect_u8("cap flood fair_state", meta.fair_state,
					FAIR_CAP_DROP);
		if (!err)
			err = run_fairness_b_committed(&env, &b_frame, &flooded_b);
	}
	if (!err)
		err = expect_counter(&env, DR_INGRESS_CAP_DROP, 1);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_CEILING_DROP, 0);
	if (!err)
		err = expect_counter(&env, DR_CONGESTION_DROP, 0);

	if (!err)
		err = setup_fairness_pair(&env, &a_config, &b_config, &node);
	for (int i = 0; !err && i < 2; i++)
		err = run_fairness_b_committed(&env, &b_frame, &clear_b);
	if (!err)
		err = expect_u64("cap flood B admission parity", flooded_b, clear_b);

	env_close(&env);
	return err;
}

static int test_fairness_ceiling_isolates_committed_neighbor(void)
{
	struct pkt_frame a_frame;
	struct pkt_frame b_frame;
	struct test_env env;
	struct fair_config a_config;
	struct fair_config b_config;
	struct fair_node_config node = {
		.version = 1,
	};
	struct pkt_meta meta;
	__u64 flooded_b = 0;
	__u64 clear_b = 0;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&a_frame) != 0 ||
	    build_default_udp_frame(&b_frame) != 0 ||
	    set_ipv4_addrs(&b_frame, TEST_SRC_PUB_B, 0x0a000003) != 0)
		return -1;
	a_config = fair_ladder_config(1, a_frame.len, a_frame.len);
	b_config = fair_ladder_config(1, b_frame.len * 6, 0);
	node.headroom_bps = a_frame.len;

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fairness_pair(&env, &a_config, &b_config, &node);
	for (int i = 0; !err && i < 3; i++) {
		err = run_frame_current_maps(&env, &a_frame, &retval);
		if (!err && i < 2)
			err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
		if (!err && i == 2)
			err = expect_u32("ceiling flood retval", retval, XDP_DROP);
		if (!err && i == 2)
			err = read_meta(&env, &meta);
		if (!err && i == 2)
			err = expect_u8("ceiling flood fair_state", meta.fair_state,
					FAIR_CEILING_DROP);
		if (!err)
			err = run_fairness_b_committed(&env, &b_frame, &flooded_b);
	}
	if (!err)
		err = expect_counter(&env, DR_INGRESS_CAP_DROP, 0);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_CEILING_DROP, 1);
	if (!err)
		err = expect_counter(&env, DR_CONGESTION_DROP, 0);

	if (!err)
		err = setup_fairness_pair(&env, &a_config, &b_config, &node);
	for (int i = 0; !err && i < 3; i++)
		err = run_fairness_b_committed(&env, &b_frame, &clear_b);
	if (!err)
		err = expect_u64("ceiling flood B admission parity", flooded_b, clear_b);

	env_close(&env);
	return err;
}

static int test_fairness_congestion_isolates_committed_neighbor(void)
{
	struct pkt_frame a_frame;
	struct pkt_frame b_frame;
	struct test_env env;
	struct fair_config a_config;
	struct fair_config b_config;
	struct fair_node_config node = {
		.version = 1,
		.headroom_bps = 0,
	};
	struct pkt_meta meta;
	__u64 flooded_b = 0;
	__u64 clear_b = 0;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&a_frame) != 0 ||
	    build_default_udp_frame(&b_frame) != 0 ||
	    set_ipv4_addrs(&b_frame, TEST_SRC_PUB_B, 0x0a000003) != 0)
		return -1;
	a_config = fair_ladder_config(1, 0, a_frame.len * 2);
	b_config = fair_ladder_config(1, b_frame.len * 4, 0);

	err = env_open(&env);
	if (err)
		return -1;

	err = setup_fairness_pair(&env, &a_config, &b_config, &node);
	for (int i = 0; !err && i < 2; i++) {
		err = run_frame_current_maps(&env, &a_frame, &retval);
		if (!err)
			err = expect_u32("congestion flood retval", retval, XDP_DROP);
		if (!err)
			err = read_meta(&env, &meta);
		if (!err)
			err = expect_u8("congestion flood fair_state", meta.fair_state,
					FAIR_CONGESTION_DROP);
		if (!err)
			err = run_fairness_b_committed(&env, &b_frame, &flooded_b);
	}
	if (!err)
		err = expect_counter(&env, DR_INGRESS_CAP_DROP, 0);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_CEILING_DROP, 0);
	if (!err)
		err = expect_counter(&env, DR_CONGESTION_DROP, 2);

	if (!err)
		err = setup_fairness_pair(&env, &a_config, &b_config, &node);
	for (int i = 0; !err && i < 2; i++)
		err = run_fairness_b_committed(&env, &b_frame, &clear_b);
	if (!err)
		err = expect_u64("congestion flood B admission parity", flooded_b,
				clear_b);

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
		err = expect_fd("whitelist_bloom_0", env.whitelist_bloom0_fd);
	if (!err)
		err = expect_fd("whitelist_bloom_1", env.whitelist_bloom1_fd);
	if (!err)
		err = expect_fd("whitelist_bloom", env.whitelist_bloom_fd);
	if (!err)
		err = expect_fd("whitelist_lpm_0", env.whitelist_lpm0_fd);
	if (!err)
		err = expect_fd("whitelist_lpm_1", env.whitelist_lpm1_fd);
	if (!err)
		err = expect_fd("whitelist_lpm", env.whitelist_lpm_fd);
	if (!err)
		err = expect_fd("vip_config_0", env.vip_config0_fd);
	if (!err)
		err = expect_fd("vip_config_1", env.vip_config1_fd);
	if (!err)
		err = expect_fd("vip_config_map", env.vip_config_map_fd);
	if (!err)
		err = expect_fd("vip_ceiling_state", env.vip_ceiling_state_fd);
	if (!err)
		err = expect_fd("fair_config_0", env.fair_config0_fd);
	if (!err)
		err = expect_fd("fair_config_1", env.fair_config1_fd);
	if (!err)
		err = expect_fd("fair_config_map", env.fair_config_map_fd);
	if (!err)
		err = expect_fd("fair_node_config", env.fair_node_config_fd);
	if (!err)
		err = expect_fd("svc_committed_state", env.svc_committed_state_fd);
	if (!err)
		err = expect_fd("svc_burst_state", env.svc_burst_state_fd);
	if (!err)
		err = expect_fd("node_burst_state", env.node_burst_state_fd);
	if (!err)
		err = expect_fd("service_ingress_cap_state",
				env.service_ingress_cap_state_fd);
	if (!err)
		err = expect_fd("svc_rl_state", env.svc_rl_state_fd);
	if (!err)
		err = expect_fd("svc_stat_map", env.svc_stat_fd);
	if (!err)
		err = expect_fd("ratelimit_config", env.ratelimit_config_fd);
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

static int test_whitelist_bloom_round_trip(void)
{
	struct wl_bloom_key present =
		test_wl_bloom_key(WL_TEST_BLOOM_PRESENT_SRC24);
	struct wl_bloom_key absent =
		test_wl_bloom_key(WL_TEST_BLOOM_ABSENT_SRC24);
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
	if (!err &&
	    bpf_map_update_elem(env.whitelist_bloom0_fd, NULL, &present,
				BPF_ANY) != 0) {
		fprintf(stderr, "failed to push whitelist bloom key: %s\n",
			strerror(errno));
		err = -1;
	}
	if (!err && bpf_map_lookup_elem(env.whitelist_bloom0_fd, NULL,
				       &present) != 0) {
		fprintf(stderr, "present bloom lookup failed: %s\n",
			strerror(errno));
		err = -1;
	}
	if (!err && bpf_map_lookup_elem(env.whitelist_bloom0_fd, NULL,
				       &absent) == 0) {
		fprintf(stderr, "absent bloom lookup unexpectedly matched\n");
		err = -1;
	}
	if (!err && errno != ENOENT) {
		fprintf(stderr, "absent bloom lookup errno: got %d, want ENOENT\n",
			errno);
		err = -1;
	}
	if (!err)
		err = set_test_trigger(&env, 2);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("present retval", retval, XDP_PASS);
	if (!err)
		err = set_test_trigger(&env, 3);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("absent retval", retval, XDP_PASS);
	if (!err)
		err = expect_counter(&env, DR_MAP_ERROR, 0);

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

static int set_ipv4_addrs(struct pkt_frame *frame, __u32 src_host,
			  __u32 dst_host)
{
	struct iphdr *iph;

	if (!frame->has_ipv4)
		return -1;

	iph = (struct iphdr *)(frame->data + frame->ipv4_off);
	iph->saddr = htonl(src_host);
	iph->daddr = htonl(dst_host);
	return 0;
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
	if (!err)
		err = expect_svc_stats_empty(&env);

	env_close(&env);
	return err;
}

static int test_bypass_service_miss_redirects_and_counts(void)
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
		err = set_node_bypass(&env, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, 0, 0);
	if (!err)
		err = expect_bypass_counter(&env, 1, frame.len);
	if (!err)
		err = expect_all_drop_counters_zero(&env);
	if (!err)
		err = expect_svc_stats_empty(&env);

	env_close(&env);
	return err;
}

static int test_bypass_preserves_parse_drops(void)
{
	struct pkt_frame ipv6;
	struct pkt_frame malformed;
	struct pkt_frame fragment;
	struct test_env env;
	struct iphdr *iph;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&ipv6);
	if (build_eth(&ipv6, ETH_P_IPV6) != 0 || build_ipv6(&ipv6) != 0)
		return -1;

	pkt_frame_init(&malformed);
	if (build_eth(&malformed, ETH_P_IP) != 0 ||
	    build_ipv4(&malformed, IPPROTO_UDP, 0, 5) != 0)
		return -1;
	iph = (struct iphdr *)(malformed.data + malformed.ipv4_off);
	iph->version = 5;

	pkt_frame_init(&fragment);
	if (build_eth(&fragment, ETH_P_IP) != 0 ||
	    build_ipv4(&fragment, IPPROTO_UDP, IPV4_MF, 5) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_node_bypass(&env, 1);
	if (!err)
		err = run_frame_current_maps(&env, &ipv6, &retval);
	if (!err)
		err = expect_u32("IPv6 retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_IPV6_UNSUPPORTED, 1);
	if (!err)
		err = expect_bypass_counter(&env, 0, 0);

	if (!err)
		err = reset_maps(&env);
	if (!err)
		err = set_node_bypass(&env, 1);
	if (!err)
		err = run_frame_current_maps(&env, &malformed, &retval);
	if (!err)
		err = expect_u32("malformed retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MALFORMED_IPV4, 1);
	if (!err)
		err = expect_bypass_counter(&env, 0, 0);

	if (!err)
		err = reset_maps(&env);
	if (!err)
		err = set_node_bypass(&env, 1);
	if (!err)
		err = run_frame_current_maps(&env, &fragment, &retval);
	if (!err)
		err = expect_u32("fragment retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_FRAGMENT_UNSUPPORTED, 1);
	if (!err)
		err = expect_bypass_counter(&env, 0, 0);

	env_close(&env);
	return err;
}

static int test_bypass_off_uses_normal_service_lookup(void)
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
		err = seed_default_enabled_service(&env);
	if (!err)
		err = set_node_bypass(&env, 0);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_bypass_counter(&env, 0, 0);
	if (!err)
		err = expect_svc_stat(&env, DEFAULT_SERVICE_ID, 1, frame.len,
				      0, 0, DR_SERVICE_MISS, 0);

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
	if (!err)
		err = expect_svc_stat(&env, 7, 0, 0, 1, frame.len,
				      DR_SERVICE_DISABLED, 1);

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
		err = expect_u8("wl_state", meta.wl_state, WL_STATE_NONE);
	if (!err)
		err = expect_all_drop_counters_zero(&env);

	env_close(&env);
	return err;
}

static int test_svc_stat_clean_counts_exact(void)
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
		err = expect_u16("frame_len", meta.frame_len, frame.len);
	if (!err)
		err = expect_svc_stat(&env, DEFAULT_SERVICE_ID, 1, frame.len,
				      0, 0, DR_SERVICE_DISABLED, 0);

	env_close(&env);
	return err;
}

static int test_svc_stat_drop_counts_exact(void)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 1,
	};
	struct svc_rl_config config = {
		.version = 1,
		.flags = SVC_RL_F_PPS_SET,
		.pps = 0,
	};
	struct pkt_frame frame;
	struct test_env env;
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
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_rule_block(&env, 0, DEFAULT_SERVICE_ID, &block);
	if (!err)
		err = seed_svc_rl_config(&env, 0, DEFAULT_SERVICE_ID, &config);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_RATE_LIMIT_DROP, 1);
	if (!err)
		err = expect_svc_stat(&env, DEFAULT_SERVICE_ID, 0, 0, 1,
				      frame.len, DR_RATE_LIMIT_DROP, 1);

	env_close(&env);
	return err;
}

static int test_whitelist_hit_bypasses_rules(void)
{
	struct vip_config config = vip_pps_config(100);
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID, TEST_SRC_PUB_A_NET24, 24,
				     &config);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("wl_state", meta.wl_state, WL_STATE_HIT_ADMIT);
	if (!err)
		err = expect_u8("rule_idx", meta.rule_idx, RULE_IDX_NONE);
	if (!err)
		err = expect_all_drop_counters_zero(&env);

	env_close(&env);
	return err;
}

static int test_whitelist_scope_does_not_cross_service(void)
{
	struct vip_config config = vip_pps_config(100);
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	__u32 service_b_dst = htonl(0x0a000003);
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000003) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID, TEST_SRC_PUB_A_NET24, 24,
				     &config);
	if (!err)
		err = seed_service_flags(&env, 0, service_b_dst, 32, 77, 1,
					 WL_F_ACTIVE);
	if (!err)
		err = seed_vip_config(&env, 0, 77, &config);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_NOT_ALLOWED, 1);
	if (!err)
		err = read_meta(&env, &meta);
	if (!err)
		err = expect_u32("service_id", meta.service_id, 77);
	if (!err)
		err = expect_u8("wl_state", meta.wl_state, WL_STATE_MISS);

	env_close(&env);
	return err;
}

static int test_whitelist_out_of_range_takes_rule_path(void)
{
	struct vip_config config = vip_pps_config(100);
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_C, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_match_all_rule_block(&env, 0, DEFAULT_SERVICE_ID);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID, TEST_SRC_PUB_A_NET24, 24,
				     &config);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("wl_state", meta.wl_state, WL_STATE_MISS);
	if (!err)
		err = expect_u8("rule_idx", meta.rule_idx, 0);

	env_close(&env);
	return err;
}

static int test_whitelist_bloom_false_positive_clean_miss(void)
{
	struct vip_config config = vip_pps_config(100);
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service_flags(&env, 0, DEFAULT_DST, 32,
					 DEFAULT_SERVICE_ID, 1, WL_F_ACTIVE);
	if (!err)
		err = seed_whitelist_bloom_key(&env, 0, DEFAULT_SERVICE_ID,
					       TEST_SRC_PUB_A_NET24);
	if (!err)
		err = seed_vip_config(&env, 0, DEFAULT_SERVICE_ID, &config);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_NOT_ALLOWED, 1);
	if (!err)
		err = read_meta(&env, &meta);
	if (!err)
		err = expect_u8("wl_state", meta.wl_state, WL_STATE_MISS);
	if (!err)
		err = expect_bloom_stat(&env, BLOOM_FP_WHITELIST, 1);

	env_close(&env);
	return err;
}

static int test_whitelist_broad_entry_skips_bloom_and_hits(void)
{
	struct vip_config config = vip_pps_config(100);
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID, TEST_SRC_PUB_A_NET16, 16,
				     &config);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("wl_state", meta.wl_state, WL_STATE_HIT_ADMIT);
	if (!err)
		err = expect_u8("rule_idx", meta.rule_idx, RULE_IDX_NONE);

	env_close(&env);
	return err;
}

static int test_whitelist_inactive_flag_treats_entries_as_clean_miss(void)
{
	struct vip_config config = vip_pps_config(100);
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_whitelist_bloom_key(&env, 0, DEFAULT_SERVICE_ID,
					       TEST_SRC_PUB_A_NET24);
	if (!err)
		err = seed_whitelist_lpm_entry(&env, 0, DEFAULT_SERVICE_ID,
					       TEST_SRC_PUB_A_NET24, 24);
	if (!err)
		err = seed_vip_config(&env, 0, DEFAULT_SERVICE_ID, &config);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_NOT_ALLOWED, 1);
	if (!err)
		err = read_meta(&env, &meta);
	if (!err)
		err = expect_u8("wl_state", meta.wl_state, WL_STATE_NONE);

	env_close(&env);
	return err;
}

static int test_whitelist_vip_config_without_set_flags_misses(void)
{
	struct vip_config config = {
		.version = 1,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID, TEST_SRC_PUB_A_NET24, 24,
				     &config);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_NOT_ALLOWED, 1);
	if (!err)
		err = read_meta(&env, &meta);
	if (!err)
		err = expect_u8("wl_state", meta.wl_state, WL_STATE_MISS);

	env_close(&env);
	return err;
}

static int test_whitelist_missing_vip_config_fails_closed(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_whitelist_bloom_key(&env, 0, DEFAULT_SERVICE_ID,
					       TEST_SRC_PUB_A_NET24);
	if (!err)
		err = seed_whitelist_lpm_entry(&env, 0, DEFAULT_SERVICE_ID,
					       TEST_SRC_PUB_A_NET24, 24);
	if (!err)
		err = set_service_wl_flags(&env, 0, DEFAULT_SERVICE_ID,
					   WL_F_ACTIVE);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MAP_ERROR, 1);

	env_close(&env);
	return err;
}

static int test_whitelist_missing_lpm_inner_fails_closed(void)
{
	struct vip_config config = vip_pps_config(100);
	struct pkt_frame frame;
	struct test_env env;
	__u32 slot = 0;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service_flags(&env, 0, DEFAULT_DST, 32,
					 DEFAULT_SERVICE_ID, 1, WL_F_ACTIVE);
	if (!err)
		err = seed_whitelist_bloom_key(&env, 0, DEFAULT_SERVICE_ID,
					       TEST_SRC_PUB_A_NET24);
	if (!err)
		err = seed_vip_config(&env, 0, DEFAULT_SERVICE_ID, &config);
	if (!err && bpf_map_delete_elem(env.whitelist_lpm_fd, &slot) != 0) {
		fprintf(stderr, "failed to delete whitelist_lpm outer slot: %s\n",
			strerror(errno));
		err = -1;
	}
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MAP_ERROR, 1);

	env_close(&env);
	return err;
}

static int test_whitelist_disabled_service_precedes_stage(void)
{
	struct vip_config config = vip_pps_config(100);
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID, TEST_SRC_PUB_A_NET24, 24,
				     &config);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_DISABLED, 1);
	if (!err)
		err = read_meta(&env, &meta);
	if (!err)
		err = expect_u8("wl_state", meta.wl_state, WL_STATE_NONE);

	env_close(&env);
	return err;
}

static int test_whitelist_gre_hit_redirects_protocol_blind(void)
{
	struct vip_config config = vip_pps_config(100);
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_GRE, 0, 5) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID, TEST_SRC_PUB_A_NET24, 24,
				     &config);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("wl_state", meta.wl_state, WL_STATE_HIT_ADMIT);
	if (!err)
		err = expect_u8("rule_idx", meta.rule_idx, RULE_IDX_NONE);

	env_close(&env);
	return err;
}

static int test_blacklist_amp_port_53_drops(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	if (build_udp_frame_ports(&frame, 53, 443) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_enabled_service_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_UDP_AMPLIFICATION_DROP, 1);
	if (!err)
		err = expect_counter(&env, DR_BOGON_DROP, 0);
	if (!err)
		err = expect_bl_state(&env, BL_STATE_AMP_HARDCODED);

	env_close(&env);
	return err;
}

static int test_blacklist_amp_port_11211_drops(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	if (build_udp_frame_ports(&frame, 11211, 443) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_enabled_service_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_UDP_AMPLIFICATION_DROP, 1);
	if (!err)
		err = expect_bl_state(&env, BL_STATE_AMP_HARDCODED);

	env_close(&env);
	return err;
}

static int test_blacklist_tcp_source_53_passes_port_filter(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_tcp_frame_ports(&frame, 53, 443) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_enabled_service_frame(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_counter(&env, DR_UDP_AMPLIFICATION_DROP, 0);
	if (!err)
		err = expect_u8("bl_state", meta.bl_state, BL_STATE_CLEAN);

	env_close(&env);
	return err;
}

static int expect_bogon_drop_for_src(__u32 src_host)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, src_host, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_enabled_service_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_BOGON_DROP, 1);
	if (!err)
		err = expect_counter(&env, DR_UDP_AMPLIFICATION_DROP, 0);
	if (!err)
		err = expect_bl_state(&env, BL_STATE_BOGON);

	env_close(&env);
	return err;
}

static int test_blacklist_bogon_rfc1918_drops(void)
{
	return expect_bogon_drop_for_src(0x0a010203);
}

static int test_blacklist_bogon_loopback_drops(void)
{
	return expect_bogon_drop_for_src(0x7f000001);
}

static int test_blacklist_bogon_multicast_drops(void)
{
	return expect_bogon_drop_for_src(0xe0000001);
}

static int test_blacklist_bogon_test_net_drops(void)
{
	return expect_bogon_drop_for_src(0xc0000209);
}

static int test_blacklist_bitmap_hit_adjacent_and_empty_pass(void)
{
	struct pkt_frame hit;
	struct pkt_frame adjacent;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_udp_frame_ports(&hit, 9999, 443) != 0 ||
	    build_udp_frame_ports(&adjacent, 9998, 443) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = seed_blocked_port(&env, 0, 9999);
	if (!err)
		err = run_frame_current_maps(&env, &hit, &retval);
	if (!err)
		err = expect_u32("hit retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_UDP_AMPLIFICATION_DROP, 1);
	if (!err)
		err = expect_bl_state(&env, BL_STATE_AMP_BITMAP);

	if (!err)
		err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = seed_blocked_port(&env, 0, 9999);
	if (!err)
		err = run_frame_current_maps(&env, &adjacent, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("adjacent bl_state", meta.bl_state, BL_STATE_CLEAN);

	if (!err)
		err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = run_frame_current_maps(&env, &hit, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("empty bl_state", meta.bl_state, BL_STATE_CLEAN);

	env_close(&env);
	return err;
}

static int test_blacklist_amp_precedes_bogon(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	if (build_udp_frame_ports(&frame, 53, 443) != 0 ||
	    set_ipv4_addrs(&frame, 0x0a010203, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = run_enabled_service_frame(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_UDP_AMPLIFICATION_DROP, 1);
	if (!err)
		err = expect_counter(&env, DR_BOGON_DROP, 0);
	if (!err)
		err = expect_bl_state(&env, BL_STATE_AMP_HARDCODED);

	env_close(&env);
	return err;
}

static int test_blacklist_bogon_precedes_bitmap(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	if (build_udp_frame_ports(&frame, 9999, 443) != 0 ||
	    set_ipv4_addrs(&frame, 0x0a010203, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = seed_blocked_port(&env, 0, 9999);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_BOGON_DROP, 1);
	if (!err)
		err = expect_counter(&env, DR_UDP_AMPLIFICATION_DROP, 0);
	if (!err)
		err = expect_bl_state(&env, BL_STATE_BOGON);

	env_close(&env);
	return err;
}

static int test_blacklist_whitelist_bypasses_amp_and_bogon(void)
{
	struct vip_config config = vip_pps_config(100);
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_udp_frame_ports(&frame, 53, 443) != 0 ||
	    set_ipv4_addrs(&frame, 0x0a010203, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID, 0x0a010200, 24,
				     &config);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("bl_state", meta.bl_state, BL_STATE_NONE);
	if (!err)
		err = expect_all_drop_counters_zero(&env);

	env_close(&env);
	return err;
}

static int test_blacklist_icmp_skips_port_filters(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	pkt_frame_init(&frame);
	if (build_eth(&frame, ETH_P_IP) != 0 ||
	    build_ipv4(&frame, IPPROTO_ICMP, 0, 5) != 0 ||
	    build_icmp(&frame, ICMP_ECHO, 0) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = seed_blocked_port(&env, 0, 0);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_counter(&env, DR_UDP_AMPLIFICATION_DROP, 0);
	if (!err)
		err = expect_u8("bl_state", meta.bl_state, BL_STATE_CLEAN);

	env_close(&env);
	return err;
}

static int test_blacklist_missing_bitmap_inner_fails_closed(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 slot = 0;
	__u32 retval = 0;
	int err;

	if (build_udp_frame_ports(&frame, 9999, 443) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err && bpf_map_delete_elem(env.blocked_port_bitmap_fd, &slot) != 0) {
		fprintf(stderr, "failed to delete bitmap outer slot: %s\n",
			strerror(errno));
		err = -1;
	}
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MAP_ERROR, 1);

	env_close(&env);
	return err;
}

static int test_blacklist_global_hit_drops_two_services(void)
{
	struct pkt_frame frame_a;
	struct pkt_frame frame_b;
	struct test_env env;
	__u32 service_b_dst = htonl(0x0a000003);
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame_a) != 0 ||
	    set_ipv4_addrs(&frame_a, TEST_SRC_PUB_C, 0x0a000002) != 0 ||
	    build_default_udp_frame(&frame_b) != 0 ||
	    set_ipv4_addrs(&frame_b, TEST_SRC_PUB_C, 0x0a000003) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_service(&env, 0, service_b_dst, 32, 77, 1);
	if (!err)
		err = seed_global_blacklist(&env, 0, TEST_SRC_PUB_C, 32);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame_a, &retval);
	if (!err)
		err = expect_u32("service A retval", retval, XDP_DROP);
	if (!err)
		err = expect_bl_state(&env, BL_STATE_GLOBAL_HIT);
	if (!err)
		err = run_frame_current_maps(&env, &frame_b, &retval);
	if (!err)
		err = expect_u32("service B retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_BLACKLIST_DROP, 2);
	if (!err)
		err = expect_bloom_stat(&env, BLOOM_FP_GLOBAL, 0);

	env_close(&env);
	return err;
}

static int test_blacklist_service_scoped_hit_does_not_cross_service(void)
{
	struct pkt_frame frame_a;
	struct pkt_frame frame_b;
	struct test_env env;
	struct pkt_meta meta;
	__u32 service_b_dst = htonl(0x0a000003);
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame_a) != 0 ||
	    set_ipv4_addrs(&frame_a, TEST_SRC_PUB_A, 0x0a000002) != 0 ||
	    build_default_udp_frame(&frame_b) != 0 ||
	    set_ipv4_addrs(&frame_b, TEST_SRC_PUB_A, 0x0a000003) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_service(&env, 0, service_b_dst, 32, 77, 1);
	if (!err)
		err = seed_match_all_rule_block(&env, 0, 77);
	if (!err)
		err = seed_service_blacklist(&env, 0, DEFAULT_SERVICE_ID,
					     TEST_SRC_PUB_A, 32);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame_a, &retval);
	if (!err)
		err = expect_u32("service A retval", retval, XDP_DROP);
	if (!err)
		err = expect_bl_state(&env, BL_STATE_SERVICE_HIT);
	if (!err)
		err = run_frame_current_maps(&env, &frame_b, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, 77, 0);
	if (!err)
		err = expect_u8("service B bl_state", meta.bl_state, BL_STATE_CLEAN);
	if (!err)
		err = expect_counter(&env, DR_BLACKLIST_DROP, 1);

	env_close(&env);
	return err;
}

static int test_blacklist_clean_miss_reaches_rules_with_active_global(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = seed_global_blacklist(&env, 0, TEST_SRC_PUB_C, 32);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("bl_state", meta.bl_state, BL_STATE_CLEAN);
	if (!err)
		err = expect_counter(&env, DR_BLACKLIST_DROP, 0);
	if (!err)
		err = expect_bloom_stat(&env, BLOOM_FP_GLOBAL, 0);

	env_close(&env);
	return err;
}

static int test_blacklist_whitelist_over_global_blacklist(void)
{
	struct vip_config config = vip_pps_config(100);
	struct pkt_frame frame_a;
	struct pkt_frame frame_b;
	struct test_env env;
	struct pkt_meta meta;
	__u32 service_b_dst = htonl(0x0a000003);
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame_a) != 0 ||
	    set_ipv4_addrs(&frame_a, TEST_SRC_PUB_A, 0x0a000002) != 0 ||
	    build_default_udp_frame(&frame_b) != 0 ||
	    set_ipv4_addrs(&frame_b, TEST_SRC_PUB_A, 0x0a000003) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_service(&env, 0, service_b_dst, 32, 77, 1);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID,
				     TEST_SRC_PUB_A_NET24, 24, &config);
	if (!err)
		err = seed_global_blacklist(&env, 0, TEST_SRC_PUB_A, 32);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame_a, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("service A bl_state", meta.bl_state, BL_STATE_NONE);
	if (!err)
		err = run_frame_current_maps(&env, &frame_b, &retval);
	if (!err)
		err = expect_u32("service B retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_BLACKLIST_DROP, 1);
	if (!err)
		err = expect_bl_state(&env, BL_STATE_GLOBAL_HIT);

	env_close(&env);
	return err;
}

static int test_blacklist_global_precedes_service_attribution(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = seed_global_blacklist(&env, 0, TEST_SRC_PUB_A, 32);
	if (!err)
		err = seed_service_blacklist(&env, 0, DEFAULT_SERVICE_ID,
					     TEST_SRC_PUB_A, 32);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_BLACKLIST_DROP, 1);
	if (!err)
		err = expect_bl_state(&env, BL_STATE_GLOBAL_HIT);

	env_close(&env);
	return err;
}

static int test_blacklist_global_bloom_false_positive_counts(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = seed_global_blacklist_bloom_key(&env, 0, TEST_SRC_PUB_A);
	if (!err)
		err = set_gbl_meta_flags(&env, 0, GBL_F_ACTIVE);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("bl_state", meta.bl_state, BL_STATE_CLEAN);
	if (!err)
		err = expect_bloom_stat(&env, BLOOM_FP_GLOBAL, 1);
	if (!err)
		err = expect_counter(&env, DR_BLACKLIST_DROP, 0);

	env_close(&env);
	return err;
}

static int test_blacklist_service_bloom_false_positive_counts(void)
{
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = seed_service_blacklist_bloom_key(&env, 0,
						       DEFAULT_SERVICE_ID,
						       TEST_SRC_PUB_A);
	if (!err)
		err = set_service_bl_flags(&env, 0, DEFAULT_SERVICE_ID,
					   BL_F_ACTIVE);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("bl_state", meta.bl_state, BL_STATE_CLEAN);
	if (!err)
		err = expect_bloom_stat(&env, BLOOM_FP_SERVICE, 1);
	if (!err)
		err = expect_counter(&env, DR_BLACKLIST_DROP, 0);

	env_close(&env);
	return err;
}

static int test_blacklist_global_broad_escape_hits_without_fp(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_C, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = seed_global_blacklist(&env, 0, 0xb9000000, 8);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_bl_state(&env, BL_STATE_GLOBAL_HIT);
	if (!err)
		err = expect_bloom_stat(&env, BLOOM_FP_GLOBAL, 0);

	env_close(&env);
	return err;
}

static int test_blacklist_missing_global_lpm_inner_fails_closed(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 slot = 0;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = seed_global_blacklist_bloom_key(&env, 0, TEST_SRC_PUB_A);
	if (!err)
		err = set_gbl_meta_flags(&env, 0, GBL_F_ACTIVE);
	if (!err && bpf_map_delete_elem(env.global_blacklist_lpm_fd, &slot) != 0) {
		fprintf(stderr, "failed to delete global LPM outer slot: %s\n",
			strerror(errno));
		err = -1;
	}
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MAP_ERROR, 1);

	env_close(&env);
	return err;
}

static int test_blacklist_missing_service_lpm_inner_fails_closed(void)
{
	struct pkt_frame frame;
	struct test_env env;
	__u32 slot = 0;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = seed_service_blacklist_bloom_key(&env, 0,
						       DEFAULT_SERVICE_ID,
						       TEST_SRC_PUB_A);
	if (!err)
		err = set_service_bl_flags(&env, 0, DEFAULT_SERVICE_ID,
					   BL_F_ACTIVE);
	if (!err && bpf_map_delete_elem(env.service_blacklist_lpm_fd, &slot) != 0) {
		fprintf(stderr, "failed to delete service LPM outer slot: %s\n",
			strerror(errno));
		err = -1;
	}
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_MAP_ERROR, 1);

	env_close(&env);
	return err;
}

static int test_vip_ceiling_pps_deterministic_terminal_drop(void)
{
	struct vip_config config = vip_pps_config(3);
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_rl_config(&env, 1);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_match_all_rule_block(&env, 0, DEFAULT_SERVICE_ID);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID, TEST_SRC_PUB_A_NET24, 24,
				     &config);
	if (!err)
		err = set_active(&env, 0, 1);

	for (int i = 0; !err && i < 5; i++) {
		err = run_frame_current_maps(&env, &frame, &retval);
		if (!err && i < 3)
			err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID,
						   0);
		if (!err && i < 3)
			err = expect_u8("wl_state", meta.wl_state,
					WL_STATE_HIT_ADMIT);
		if (!err && i < 3)
			err = expect_u8("rule_idx", meta.rule_idx, RULE_IDX_NONE);
		if (!err && i >= 3)
			err = expect_u32("retval", retval, XDP_DROP);
		if (!err && i >= 3)
			err = read_meta(&env, &meta);
		if (!err && i >= 3)
			err = expect_u8("drop wl_state", meta.wl_state,
					WL_STATE_HIT_DROP);
		if (!err && i >= 3)
			err = expect_u8("drop rule_idx", meta.rule_idx,
					RULE_IDX_NONE);
	}
	if (!err)
		err = expect_counter(&env, DR_VIP_CEILING_DROP, 2);
	if (!err)
		err = expect_counter(&env, DR_NOT_ALLOWED, 0);
	if (!err)
		err = expect_counter(&env, DR_RATE_LIMIT_DROP, 0);

	env_close(&env);
	return err;
}

static int test_vip_ceiling_pps_zero_blocks(void)
{
	struct vip_config config = vip_pps_config(0);
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_rl_config(&env, 1);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID, TEST_SRC_PUB_A_NET24, 24,
				     &config);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_VIP_CEILING_DROP, 1);
	if (!err)
		err = read_meta(&env, &meta);
	if (!err)
		err = expect_u8("wl_state", meta.wl_state, WL_STATE_HIT_DROP);

	env_close(&env);
	return err;
}

static int test_vip_ceiling_pps_drop_preserves_bps_tokens(void)
{
	struct vip_config config = {
		.version = 1,
		.flags = VIP_F_PPS_SET | VIP_F_BPS_SET,
		.pps = 1,
	};
	struct rl_bucket bucket;
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u64 bps_budget;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;
	bps_budget = frame.len * 10;
	config.bps = bps_budget;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_rl_config(&env, 1);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID, TEST_SRC_PUB_A_NET24, 24,
				     &config);
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
		err = read_vip_bucket_cpu0(&env, DEFAULT_SERVICE_ID, &bucket);
	if (!err)
		err = expect_u32("bps_tokens preserved",
				 (__u32)bucket.bps_tokens,
				 (__u32)(bps_budget - frame.len));

	env_close(&env);
	return err;
}

static int test_vip_ceiling_aggregate_budget_across_sources(void)
{
	struct vip_config config = vip_pps_config(5);
	struct pkt_frame frame_a;
	struct pkt_frame frame_b;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame_a) != 0 ||
	    set_ipv4_addrs(&frame_a, TEST_SRC_PUB_A, 0x0a000002) != 0 ||
	    build_default_udp_frame(&frame_b) != 0 ||
	    set_ipv4_addrs(&frame_b, TEST_SRC_PUB_B, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_rl_config(&env, 1);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID, TEST_SRC_PUB_A_NET24, 24,
				     &config);
	if (!err)
		err = set_active(&env, 0, 1);

	for (int i = 0; !err && i < 6; i++) {
		const struct pkt_frame *frame = i % 2 ? &frame_b : &frame_a;

		err = run_frame_current_maps(&env, frame, &retval);
		if (!err && i < 5)
			err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID,
						   0);
		if (!err && i < 5)
			err = expect_u8("wl_state", meta.wl_state,
					WL_STATE_HIT_ADMIT);
		if (!err && i >= 5)
			err = expect_u32("retval", retval, XDP_DROP);
	}
	if (!err)
		err = expect_counter(&env, DR_VIP_CEILING_DROP, 1);

	env_close(&env);
	return err;
}

static int test_vip_ceiling_reset_on_config_version(void)
{
	struct vip_config config = vip_pps_config(1);
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = set_rl_config(&env, 1);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID, TEST_SRC_PUB_A_NET24, 24,
				     &config);
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
		config.version = 2;
		err = seed_vip_config(&env, 0, DEFAULT_SERVICE_ID, &config);
	}
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_counter(&env, DR_VIP_CEILING_DROP, 1);

	env_close(&env);
	return err;
}

static int test_vip_ceiling_normal_mode_fresh_bucket_admits(void)
{
	struct vip_config config = vip_pps_config(1);
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_A, 0x0a000002) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = seed_whitelist(&env, 0, DEFAULT_SERVICE_ID, TEST_SRC_PUB_A_NET24, 24,
				     &config);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u8("wl_state", meta.wl_state, WL_STATE_HIT_ADMIT);
	if (!err)
		err = expect_counter(&env, DR_VIP_CEILING_DROP, 0);

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
	struct svc_rl_config config = {
		.version = 1,
		.flags = SVC_RL_F_PPS_SET,
		.pps = 3,
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
		err = seed_svc_rl_config(&env, 0, DEFAULT_SERVICE_ID, &config);
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
	struct svc_rl_config config = {
		.version = 1,
		.flags = SVC_RL_F_PPS_SET,
		.pps = 1,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct pkt_meta meta;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	block.rules[0] = default_udp_rule();
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
		err = seed_svc_rl_config(&env, 0, DEFAULT_SERVICE_ID, &config);
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
	struct svc_rl_config config = {
		.version = 1,
		.flags = SVC_RL_F_PPS_SET,
		.pps = 0,
	};
	struct pkt_frame frame;
	struct test_env env;
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
		err = seed_svc_rl_config(&env, 0, DEFAULT_SERVICE_ID, &config);
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
	struct svc_rl_config config = {
		.version = 1,
		.flags = SVC_RL_F_BPS_SET,
		.bps = frame.len * 2,
	};

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
		err = seed_svc_rl_config(&env, 0, DEFAULT_SERVICE_ID, &config);
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
	struct svc_rl_config config = {
		.version = 1,
		.flags = SVC_RL_F_PPS_SET | SVC_RL_F_BPS_SET,
		.pps = 1,
		.bps = bps_budget,
	};

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
		err = seed_svc_rl_config(&env, 0, DEFAULT_SERVICE_ID, &config);
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
		err = read_svc_rl_bucket_cpu0(&env, DEFAULT_SERVICE_ID, &bucket);
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
	struct svc_rl_config config = {
		.version = 1,
		.flags = SVC_RL_F_PPS_SET,
		.pps = 1,
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
		err = seed_svc_rl_config(&env, 0, DEFAULT_SERVICE_ID, &config);
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
		config.version = 2;
		err = seed_svc_rl_config(&env, 0, DEFAULT_SERVICE_ID, &config);
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
	struct svc_rl_config config = {
		.version = 1,
		.flags = SVC_RL_F_PPS_SET,
		.pps = 1,
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
		err = seed_svc_rl_config(&env, 0, DEFAULT_SERVICE_ID, &config);
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
		err = expect_u32("src_ip", meta.src_ip, DEFAULT_SRC);
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
	if (!err)
		err = expect_svc_stats_empty(&env);

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
	if (!err)
		err = expect_svc_stats_empty(&env);

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

static struct cfg_service apply_cfg_service(__u32 dst_host, __u32 dp_id,
					    __u8 enabled)
{
	struct cfg_service service = {
		.dst_prefixlen = 32,
		.dst_addr = htonl(dst_host),
		.dp_id = dp_id,
		.enabled = enabled,
		.committed_bps = 1000000000ULL,
		.ceiling_bps = 1000000000ULL,
	};

	return service;
}

static void apply_cfg_match_all(struct cfg_service *service)
{
	service->rule_count = 1;
	service->rules[0] = (struct cfg_rule){
		.src_lo = 0,
		.src_hi = UINT16_MAX,
		.dst_lo = 0,
		.dst_hi = UINT16_MAX,
		.proto = RULE_PROTO_ANY,
		.flags = RULE_F_ENABLED,
	};
}

static struct apply_fds apply_fds_for_env(struct test_env *env)
{
	return (struct apply_fds){
		.active_config_fd = env->active_config_fd,
		.service_map_fd = env->service_map_fd,
		.rule_block_map_fd = env->rule_block_map_fd,
		.whitelist_bloom_fd = env->whitelist_bloom_fd,
		.whitelist_lpm_fd = env->whitelist_lpm_fd,
		.vip_config_map_fd = env->vip_config_map_fd,
		.global_blacklist_bloom_fd = env->global_blacklist_bloom_fd,
		.global_blacklist_lpm_fd = env->global_blacklist_lpm_fd,
		.service_blacklist_bloom_fd = env->service_blacklist_bloom_fd,
		.service_blacklist_lpm_fd = env->service_blacklist_lpm_fd,
		.udp_blocked_port_bitmap_fd = env->blocked_port_bitmap_fd,
		.fair_config_map_fd = env->fair_config_map_fd,
		.fair_node_config_fd = env->fair_node_config_fd,
		.gbl_meta_fd = env->gbl_meta_fd,
	};
}

static int apply_cfg_core(struct apply_fds *fds, const struct node_cfg *node)
{
	if (build_inactive_slot(fds, node) != 0 ||
	    carry_forward_feed(fds) != 0 || verify_slot(fds) != 0 ||
	    commit(fds) != 0)
		return -1;
	return 0;
}

static int apply_inner_fd_for_test(int outer_fd, __u32 slot)
{
	__u32 inner_id = 0;

	if (bpf_map_lookup_elem(outer_fd, &slot, &inner_id) != 0)
		return -1;
	return bpf_map_get_fd_by_id(inner_id);
}

static int test_apply_swap_adds_allow_rule(void)
{
	struct cfg_service service = apply_cfg_service(0x0a000002,
						       DEFAULT_SERVICE_ID, 1);
	struct node_cfg node = {
		.schema_version = APPLY_SNAPSHOT_SCHEMA_VERSION,
		.service_count = 1,
		.services = &service,
	};
	struct active_config active;
	struct pkt_frame frame;
	struct pkt_meta meta;
	struct test_env env;
	struct apply_fds fds;
	__u32 zero = 0;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	apply_cfg_match_all(&service);

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = set_active(&env, 0, 7);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("pre-swap retval", retval, XDP_DROP);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_cfg_core(&fds, &node);
	}
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 1);
	if (!err && bpf_map_lookup_elem(env.active_config_fd, &zero, &active) != 0)
		err = -1;
	if (!err)
		err = expect_u32("active slot", active.active_slot, 1);
	if (!err)
		err = expect_u32("active version", active.version, 8);

	env_close(&env);
	return err;
}

static int test_apply_swap_adds_service_and_preserves_existing(void)
{
	struct cfg_service services[2] = {
		apply_cfg_service(0x0a000002, DEFAULT_SERVICE_ID, 1),
		apply_cfg_service(0x0a000003, 77, 1),
	};
	struct node_cfg node = {
		.schema_version = APPLY_SNAPSHOT_SCHEMA_VERSION,
		.service_count = 2,
		.services = services,
	};
	struct pkt_frame existing;
	struct pkt_frame added;
	struct pkt_meta meta;
	struct test_env env;
	struct apply_fds fds;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&existing) != 0 ||
	    build_default_udp_frame(&added) != 0 ||
	    set_ipv4_addrs(&added, TEST_SRC_PUB_A, 0x0a000003) != 0)
		return -1;
	apply_cfg_match_all(&services[0]);
	apply_cfg_match_all(&services[1]);

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = set_active(&env, 0, 1);
	if (!err)
		err = run_frame_current_maps(&env, &existing, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = run_frame_current_maps(&env, &added, &retval);
	if (!err)
		err = expect_u32("new service pre-swap", retval, XDP_DROP);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_cfg_core(&fds, &node);
	}
	if (!err)
		err = run_frame_current_maps(&env, &existing, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = run_frame_current_maps(&env, &added, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, 77, 1);

	env_close(&env);
	return err;
}

static int test_apply_swap_disables_service(void)
{
	struct cfg_service service = apply_cfg_service(0x0a000002,
						       DEFAULT_SERVICE_ID, 0);
	struct node_cfg node = {
		.schema_version = APPLY_SNAPSHOT_SCHEMA_VERSION,
		.service_count = 1,
		.services = &service,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct apply_fds fds;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	apply_cfg_match_all(&service);

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_cfg_core(&fds, &node);
	}
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("disabled service retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_DISABLED, 1);

	env_close(&env);
	return err;
}

static int test_apply_swap_removes_service(void)
{
	struct node_cfg node = {
		.schema_version = APPLY_SNAPSHOT_SCHEMA_VERSION,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct apply_fds fds;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_cfg_core(&fds, &node);
	}
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("removed service retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_SERVICE_MISS, 1);

	env_close(&env);
	return err;
}

static int test_apply_swap_carries_global_blacklist(void)
{
	struct cfg_service service = apply_cfg_service(0x0a000002,
						       DEFAULT_SERVICE_ID, 1);
	struct node_cfg node = {
		.schema_version = APPLY_SNAPSHOT_SCHEMA_VERSION,
		.service_count = 1,
		.services = &service,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct apply_fds fds;
	__u32 before_id = 0;
	__u32 after_id = 0;
	__u32 slot0 = 0;
	__u32 slot1 = 1;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_C, 0x0a000002) != 0)
		return -1;
	apply_cfg_match_all(&service);

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = seed_global_blacklist(&env, 0, TEST_SRC_PUB_C, 32);
	if (!err &&
	    bpf_map_lookup_elem(env.global_blacklist_lpm_fd, &slot0, &before_id) !=
		0)
		err = -1;
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_cfg_core(&fds, &node);
	}
	if (!err &&
	    bpf_map_lookup_elem(env.global_blacklist_lpm_fd, &slot1, &after_id) !=
		0)
		err = -1;
	if (!err)
		err = expect_u32("carried global LPM id", after_id, before_id);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("carried global blacklist retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_BLACKLIST_DROP, 1);

	env_close(&env);
	return err;
}

static int test_apply_verify_rejects_structural_mismatches(void)
{
	struct cfg_service service = apply_cfg_service(0x0a000002,
						       DEFAULT_SERVICE_ID, 1);
	struct node_cfg node = {
		.schema_version = APPLY_SNAPSHOT_SCHEMA_VERSION,
		.service_count = 1,
		.services = &service,
	};
	struct service_key service_key = {
		.prefixlen = 32,
		.addr = DEFAULT_DST,
	};
	struct rule_block block;
	struct test_env env;
	struct apply_fds fds;
	__u32 inactive = 1;
	int service_fd = -1;
	int rule_fd = -1;
	int err;

	apply_cfg_match_all(&service);
	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = build_inactive_slot(&fds, &node);
	}
	if (!err)
		err = carry_forward_feed(&fds);
	if (!err)
		err = verify_slot(&fds);
	if (!err && (service_fd =
		     apply_inner_fd_for_test(env.service_map_fd, inactive)) < 0) {
		err = -1;
	}
	if (!err && bpf_map_delete_elem(service_fd, &service_key) != 0)
		err = -1;
	if (!err && verify_slot(&fds) == 0)
		err = -1;
	if (service_fd >= 0)
		close(service_fd);

	if (!err)
		err = reset_maps(&env);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = build_inactive_slot(&fds, &node);
	}
	if (!err)
		err = carry_forward_feed(&fds);
	if (!err && (rule_fd =
		     apply_inner_fd_for_test(env.rule_block_map_fd, inactive)) < 0) {
		err = -1;
	}
	if (!err && bpf_map_lookup_elem(rule_fd, &service.dp_id, &block) != 0)
		err = -1;
	if (!err) {
		block.version++;
		if (bpf_map_update_elem(rule_fd, &service.dp_id, &block, BPF_ANY) != 0)
			err = -1;
	}
	if (!err && verify_slot(&fds) == 0)
		err = -1;
	if (rule_fd >= 0)
		close(rule_fd);

	if (!err)
		err = reset_maps(&env);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = build_inactive_slot(&fds, &node);
	}
	if (!err)
		err = carry_forward_feed(&fds);
	if (!err &&
	    bpf_map_delete_elem(env.whitelist_lpm_fd, &inactive) != 0)
		err = -1;
	if (!err && verify_slot(&fds) == 0)
		err = -1;

	env_close(&env);
	return err;
}

/*
 * DBS-09 de-risk: prove the one novel composition xdpgw-apply stands on —
 * create a fresh inner meta-equal to the live slot-0 inner via create_inner_like,
 * populate it, install it into the inactive slot (1) of a statically-initialised
 * ARRAY_OF_MAPS outer, then look it back up through the outer and read the key.
 * If userspace inner-replacement into a pinned/static outer did not work, this
 * fails fast at the first Execute gate (AD-028 fallback ladder).
 */
static int test_apply_fresh_inner_install(void)
{
	const __u32 inactive_slot = 1;
	const __u32 service_id = 4242;
	struct test_env env;
	int fresh_fd = -1;
	int got_fd = -1;
	int err;

	err = env_open(&env);
	if (err)
		return -1;

	fresh_fd = create_inner_like(env.rule_block_map_fd, 0);
	if (!err)
		err = expect_fd("fresh rule_block inner", fresh_fd);

	if (!err) {
		struct rule_block block;

		memset(&block, 0, sizeof(block));
		block.version = 7;
		block.rule_count = 1;
		block.rules[0].flags = RULE_F_ENABLED;
		if (bpf_map_update_elem(fresh_fd, &service_id, &block,
					BPF_ANY) != 0) {
			fprintf(stderr, "populate fresh inner: %s\n",
				strerror(errno));
			err = -1;
		}
	}

	if (!err &&
	    bpf_map_update_elem(env.rule_block_map_fd, &inactive_slot, &fresh_fd,
				BPF_ANY) != 0) {
		fprintf(stderr, "install fresh inner into outer: %s\n",
			strerror(errno));
		err = -1;
	}

	if (!err) {
		struct rule_block got;
		__u32 inner_id = 0;

		memset(&got, 0, sizeof(got));
		if (bpf_map_lookup_elem(env.rule_block_map_fd, &inactive_slot,
					&inner_id) != 0) {
			fprintf(stderr, "look up installed inner: %s\n",
				strerror(errno));
			err = -1;
		} else if ((got_fd = bpf_map_get_fd_by_id(inner_id)) < 0) {
			fprintf(stderr, "fd for installed inner id: %s\n",
				strerror(errno));
			err = -1;
		} else if (bpf_map_lookup_elem(got_fd, &service_id, &got) != 0) {
			fprintf(stderr, "read key back from inner: %s\n",
				strerror(errno));
			err = -1;
		} else if (got.version != 7 || got.rule_count != 1 ||
			   got.rules[0].flags != RULE_F_ENABLED) {
			fprintf(stderr, "installed inner read-back mismatch\n");
			err = -1;
		}
	}

	if (got_fd >= 0)
		close(got_fd);
	if (fresh_fd >= 0)
		close(fresh_fd);
	env_close(&env);
	return err;
}

static int expect_active_config(struct test_env *env, __u32 slot,
				__u32 version)
{
	struct active_config active;
	__u32 zero = 0;

	if (bpf_map_lookup_elem(env->active_config_fd, &zero, &active) != 0)
		return -1;
	if (expect_u32("active slot", active.active_slot, slot) != 0)
		return -1;
	return expect_u32("active version", active.version, version);
}

static int test_apply_forced_failures_preserve_live_slot(void)
{
	struct cfg_service service = apply_cfg_service(0x0a000002,
						       DEFAULT_SERVICE_ID, 1);
	struct node_cfg node = {
		.schema_version = APPLY_SNAPSHOT_SCHEMA_VERSION,
		.service_count = 1,
		.services = &service,
	};
	struct pkt_frame frame;
	struct pkt_meta meta;
	struct test_env env;
	struct apply_fds fds;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	apply_cfg_match_all(&service);
	apply_test_set_fault(APPLY_TEST_FAULT_NONE);

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = set_active(&env, 0, 7);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err) {
		fds = apply_fds_for_env(&env);
		apply_test_set_fault(APPLY_TEST_FAULT_BUILD_INSTALL);
		if (apply_node_cfg(&fds, &node) == 0)
			err = -1;
	}
	apply_test_set_fault(APPLY_TEST_FAULT_NONE);
	if (!err)
		err = expect_active_config(&env, 0, 7);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u32("open fresh fds after build failure",
				 apply_test_fresh_fd_count(), 0);

	if (!err)
		err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = set_active(&env, 0, 9);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err) {
		fds = apply_fds_for_env(&env);
		apply_test_set_fault(APPLY_TEST_FAULT_VERIFY_MISMATCH);
		if (apply_node_cfg(&fds, &node) == 0)
			err = -1;
	}
	apply_test_set_fault(APPLY_TEST_FAULT_NONE);
	if (!err)
		err = expect_active_config(&env, 0, 9);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);
	if (!err)
		err = expect_u32("open fresh fds after verify failure",
				 apply_test_fresh_fd_count(), 0);

	apply_test_set_fault(APPLY_TEST_FAULT_NONE);
	env_close(&env);
	return err;
}

static int test_apply_interruption_before_commit_preserves_live_slot(void)
{
	struct cfg_service service = apply_cfg_service(0x0a000002,
						       DEFAULT_SERVICE_ID, 1);
	struct node_cfg node = {
		.schema_version = APPLY_SNAPSHOT_SCHEMA_VERSION,
		.service_count = 1,
		.services = &service,
	};
	struct pkt_frame frame;
	struct pkt_meta meta;
	struct test_env env;
	struct apply_fds fds;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	apply_cfg_match_all(&service);

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = set_active(&env, 0, 10);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("pre-interrupt retval", retval, XDP_DROP);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = build_inactive_slot(&fds, &node);
	}
	if (!err)
		err = carry_forward_feed(&fds);
	if (!err)
		err = verify_slot(&fds);
	if (!err)
		err = expect_active_config(&env, 0, 10);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("interrupted retval", retval, XDP_DROP);

	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_node_cfg(&fds, &node);
	}
	if (!err)
		err = expect_active_config(&env, 1, 11);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 1);

	env_close(&env);
	return err;
}

static int test_apply_same_snapshot_toggles_slots_and_versions(void)
{
	struct cfg_service service = apply_cfg_service(0x0a000002,
						       DEFAULT_SERVICE_ID, 1);
	struct node_cfg node = {
		.schema_version = APPLY_SNAPSHOT_SCHEMA_VERSION,
		.service_count = 1,
		.services = &service,
	};
	struct pkt_frame frame;
	struct pkt_meta meta;
	struct test_env env;
	struct apply_fds fds;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0)
		return -1;
	apply_cfg_match_all(&service);

	err = env_open(&env);
	if (err)
		return -1;

	err = reset_maps(&env);
	if (!err)
		err = seed_service(&env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1);
	if (!err)
		err = set_active(&env, 0, 20);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_node_cfg(&fds, &node);
	}
	if (!err)
		err = expect_active_config(&env, 1, 21);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 1);

	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_node_cfg(&fds, &node);
	}
	if (!err)
		err = expect_active_config(&env, 0, 22);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &meta, DEFAULT_SERVICE_ID, 0);

	env_close(&env);
	return err;
}

static struct node_cfg global_deny_cfg(struct cfg_source *entries,
				       __u32 count)
{
	return (struct node_cfg){
		.schema_version = APPLY_SNAPSHOT_SCHEMA_VERSION,
		.snapshot_kind = APPLY_SNAPSHOT_KIND_GLOBAL_DENY,
		.global_revision = 91,
		.global_count = count,
		.global_entries = entries,
	};
}

static int global_outer_id(int outer_fd, __u32 slot, __u32 *id)
{
	return bpf_map_lookup_elem(outer_fd, &slot, id);
}

static int test_global_apply_rebuilds_hot_path_blacklist(void)
{
	struct cfg_source entries[] = {
		{ .prefixlen = 32, .addr = htonl(TEST_SRC_PUB_C) },
	};
	struct node_cfg node = global_deny_cfg(entries, 1);
	struct pkt_frame frame;
	struct test_env env;
	struct apply_fds fds;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_C, 0x0a000002) != 0)
		return -1;
	err = env_open(&env);
	if (err)
		return -1;
	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_global_deny_cfg(&fds, &node);
	}
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("global blacklist retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_BLACKLIST_DROP, 1);
	env_close(&env);
	return err;
}

static int test_global_apply_carries_service_outers_and_bitmap(void)
{
	struct cfg_source entries[] = {
		{ .prefixlen = 32, .addr = htonl(TEST_SRC_PUB_C) },
	};
	int service_outers[] = {
		APPLY_SERVICE_MAP,
		APPLY_RULE_BLOCK_MAP,
		APPLY_WHITELIST_BLOOM,
		APPLY_WHITELIST_LPM,
		APPLY_VIP_CONFIG_MAP,
		APPLY_SERVICE_BLACKLIST_BLOOM,
		APPLY_SERVICE_BLACKLIST_LPM,
		APPLY_FAIR_CONFIG_MAP,
	};
	int outer_fds[] = {
		0, 0, 0, 0, 0, 0, 0, 0,
	};
	struct node_cfg node = global_deny_cfg(entries, 1);
	struct fair_node_config before_node = {
		.version = 7,
		.headroom_bps = 123456,
	};
	struct fair_node_config after_node;
	struct test_env env;
	struct apply_fds fds;
	__u32 before_id[APPLY_SERVICE_OUTER_COUNT];
	__u32 after_id;
	__u32 bitmap_before;
	__u32 bitmap_after;
	__u32 slot0 = 0;
	__u32 slot1 = 1;
	int err;

	(void)service_outers;
	err = env_open(&env);
	if (err)
		return -1;
	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = seed_blocked_port(&env, 0, 9999);
	if (!err)
		err = seed_fair_node_config(&env, 0, &before_node);
	outer_fds[APPLY_SERVICE_MAP] = env.service_map_fd;
	outer_fds[APPLY_RULE_BLOCK_MAP] = env.rule_block_map_fd;
	outer_fds[APPLY_WHITELIST_BLOOM] = env.whitelist_bloom_fd;
	outer_fds[APPLY_WHITELIST_LPM] = env.whitelist_lpm_fd;
	outer_fds[APPLY_VIP_CONFIG_MAP] = env.vip_config_map_fd;
	outer_fds[APPLY_SERVICE_BLACKLIST_BLOOM] = env.service_blacklist_bloom_fd;
	outer_fds[APPLY_SERVICE_BLACKLIST_LPM] = env.service_blacklist_lpm_fd;
	outer_fds[APPLY_FAIR_CONFIG_MAP] = env.fair_config_map_fd;
	for (__u32 i = 0; !err && i < APPLY_SERVICE_OUTER_COUNT; i++)
		err = global_outer_id(outer_fds[i], slot0, &before_id[i]);
	if (!err)
		err = global_outer_id(env.blocked_port_bitmap_fd, slot0,
				      &bitmap_before);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_global_deny_cfg(&fds, &node);
	}
	for (__u32 i = 0; !err && i < APPLY_SERVICE_OUTER_COUNT; i++) {
		err = global_outer_id(outer_fds[i], slot1, &after_id);
		if (!err)
			err = expect_u32("carried service outer id", after_id,
					 before_id[i]);
	}
	if (!err)
		err = global_outer_id(env.blocked_port_bitmap_fd, slot1,
				      &bitmap_after);
	if (!err)
		err = expect_u32("carried bitmap id", bitmap_after, bitmap_before);
	if (!err && bpf_map_lookup_elem(env.fair_node_config_fd, &slot1,
					  &after_node) != 0)
		err = -1;
	if (!err && memcmp(&before_node, &after_node, sizeof(before_node)) != 0)
		err = -1;
	env_close(&env);
	return err;
}

static int test_global_apply_expands_16_to_bloom_coverage(void)
{
	struct cfg_source entries[] = {
		{ .prefixlen = 16, .addr = htonl(0x2d2d0000) },
	};
	struct node_cfg node = global_deny_cfg(entries, 1);
	struct gbl_meta meta;
	struct pkt_frame frame;
	struct test_env env;
	struct apply_fds fds;
	__u32 slot = 1;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, 0x2d2d7f01, 0x0a000002) != 0)
		return -1;
	err = env_open(&env);
	if (err)
		return -1;
	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_global_deny_cfg(&fds, &node);
	}
	if (!err && bpf_map_lookup_elem(env.gbl_meta_fd, &slot, &meta) != 0)
		err = -1;
	if (!err)
		err = expect_u8("expanded /16 flags", meta.flags, GBL_F_ACTIVE);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("expanded /16 retval", retval, XDP_DROP);
	env_close(&env);
	return err;
}

static int test_global_apply_broad_prefix_sets_escape_flag(void)
{
	struct cfg_source entries[] = {
		{ .prefixlen = 8, .addr = htonl(0x2d000000) },
	};
	struct node_cfg node = global_deny_cfg(entries, 1);
	struct gbl_meta meta;
	struct pkt_frame frame;
	struct test_env env;
	struct apply_fds fds;
	__u32 slot = 1;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, 0x2d040302, 0x0a000002) != 0)
		return -1;
	err = env_open(&env);
	if (err)
		return -1;
	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_global_deny_cfg(&fds, &node);
	}
	if (!err && bpf_map_lookup_elem(env.gbl_meta_fd, &slot, &meta) != 0)
		err = -1;
	if (!err)
		err = expect_u8("broad flags", meta.flags,
				GBL_F_ACTIVE | GBL_F_HAS_BROAD);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("broad global retval", retval, XDP_DROP);
	env_close(&env);
	return err;
}

static int test_global_apply_empty_snapshot_clears_active_meta(void)
{
	struct node_cfg node = global_deny_cfg(NULL, 0);
	struct gbl_meta meta;
	struct pkt_frame frame;
	struct test_env env;
	struct apply_fds fds;
	__u32 slot = 1;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_C, 0x0a000002) != 0)
		return -1;
	err = env_open(&env);
	if (err)
		return -1;
	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = seed_global_blacklist(&env, 0, TEST_SRC_PUB_C, 32);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_global_deny_cfg(&fds, &node);
	}
	if (!err && bpf_map_lookup_elem(env.gbl_meta_fd, &slot, &meta) != 0)
		err = -1;
	if (!err)
		err = expect_u8("empty global flags", meta.flags, 0);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_redirect_meta(&env, &(struct pkt_meta){},
					DEFAULT_SERVICE_ID, slot);
	env_close(&env);
	return err;
}

static int test_global_apply_build_failure_preserves_live_slot(void)
{
	struct cfg_source entries[] = {
		{ .prefixlen = 32, .addr = htonl(TEST_SRC_PUB_C) },
	};
	struct node_cfg node = global_deny_cfg(entries, 1);
	struct test_env env;
	struct apply_fds fds;
	int err;

	err = env_open(&env);
	if (err)
		return -1;
	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = set_active(&env, 0, 51);
	if (!err) {
		fds = apply_fds_for_env(&env);
		apply_test_set_fault(APPLY_TEST_FAULT_BUILD_INSTALL);
		if (apply_global_deny_cfg(&fds, &node) == 0)
			err = -1;
	}
	apply_test_set_fault(APPLY_TEST_FAULT_NONE);
	if (!err)
		err = expect_active_config(&env, 0, 51);
	if (!err)
		err = expect_u32("global build failure fresh fds",
				 apply_test_fresh_fd_count(), 0);
	env_close(&env);
	return err;
}

static int test_global_apply_verify_failure_preserves_live_slot(void)
{
	struct cfg_source entries[] = {
		{ .prefixlen = 32, .addr = htonl(TEST_SRC_PUB_C) },
	};
	struct node_cfg node = global_deny_cfg(entries, 1);
	struct test_env env;
	struct apply_fds fds;
	int err;

	err = env_open(&env);
	if (err)
		return -1;
	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = set_active(&env, 0, 52);
	if (!err) {
		fds = apply_fds_for_env(&env);
		apply_test_set_fault(APPLY_TEST_FAULT_VERIFY_MISMATCH);
		if (apply_global_deny_cfg(&fds, &node) == 0)
			err = -1;
	}
	apply_test_set_fault(APPLY_TEST_FAULT_NONE);
	if (!err)
		err = expect_active_config(&env, 0, 52);
	if (!err)
		err = expect_u32("global verify failure fresh fds",
				 apply_test_fresh_fd_count(), 0);
	env_close(&env);
	return err;
}

static int test_global_apply_alternates_with_service_apply(void)
{
	struct cfg_source entries[] = {
		{ .prefixlen = 32, .addr = htonl(TEST_SRC_PUB_C) },
	};
	struct cfg_service service = apply_cfg_service(0x0a000002,
					       DEFAULT_SERVICE_ID, 1);
	struct node_cfg global = global_deny_cfg(entries, 1);
	struct node_cfg node = {
		.schema_version = APPLY_SNAPSHOT_SCHEMA_VERSION,
		.snapshot_kind = APPLY_SNAPSHOT_KIND_SERVICE_FULL,
		.service_count = 1,
		.services = &service,
	};
	struct pkt_frame frame;
	struct test_env env;
	struct apply_fds fds;
	__u32 retval = 0;
	int err;

	if (build_default_udp_frame(&frame) != 0 ||
	    set_ipv4_addrs(&frame, TEST_SRC_PUB_C, 0x0a000002) != 0)
		return -1;
	apply_cfg_match_all(&service);
	err = env_open(&env);
	if (err)
		return -1;
	err = reset_maps(&env);
	if (!err)
		err = seed_default_enabled_service(&env);
	if (!err)
		err = set_active(&env, 0, 60);
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_node_cfg(&fds, &node);
	}
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_global_deny_cfg(&fds, &global);
	}
	if (!err) {
		fds = apply_fds_for_env(&env);
		err = apply_node_cfg(&fds, &node);
	}
	if (!err)
		err = expect_active_config(&env, 1, 63);
	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("alternating global retval", retval, XDP_DROP);
	env_close(&env);
	return err;
}

static int test_nexthop_redirect_success(void)
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
		err = seed_default_enabled_service(&env);

	struct nexthop nh = {
		.dst_mac = {0x00, 0xaa, 0xbb, 0xcc, 0xdd, 0xee},
		.src_mac = {0x00, 0x11, 0x22, 0x33, 0x44, 0x55},
		.resolved = 1,
		.last_resolved_ns = 123456,
	};
	__u32 key = DEFAULT_SERVICE_ID;
	if (!err)
		err = bpf_map_update_elem(env.nexthop_map_fd, &key, &nh, BPF_ANY);

	__u32 dev_key = 0;
	__u32 dev_val = 1;
	if (!err)
		err = bpf_map_update_elem(env.tx_devmap_fd, &dev_key, &dev_val, BPF_ANY);

	uint8_t data_out[512] = {};
	struct bpf_test_run_opts opts = {
		.sz = sizeof(opts),
		.data_in = frame.data,
		.data_size_in = frame.len,
		.data_out = data_out,
		.data_size_out = sizeof(data_out),
		.repeat = 1,
	};

	if (!err) {
		err = bpf_prog_test_run_opts(env.prog_fd, &opts);
		if (err) {
			fprintf(stderr, "BPF_PROG_TEST_RUN failed: %s\n", strerror(errno));
			err = -1;
		} else {
			retval = opts.retval;
		}
	}

	if (!err)
		err = expect_u32("retval", retval, XDP_REDIRECT);

	if (!err) {
		struct ethhdr *eth = (struct ethhdr *)data_out;
		if (memcmp(eth->h_dest, nh.dst_mac, ETH_ALEN) != 0 ||
		    memcmp(eth->h_source, nh.src_mac, ETH_ALEN) != 0) {
			fprintf(stderr, "error: L2 rewrite mismatch\n");
			err = -1;
		}
	}

	if (!err) {
		struct iphdr *iph = (struct iphdr *)(data_out + sizeof(struct ethhdr));
		if (iph->ttl != 64) {
			fprintf(stderr, "error: IP TTL changed to %d\n", iph->ttl);
			err = -1;
		}
	}

	env_close(&env);
	return err;
}

static int test_nexthop_unresolved_drop(void)
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
		err = seed_default_enabled_service(&env);

	struct nexthop nh = {
		.dst_mac = {0},
		.src_mac = {0},
		.resolved = 0,
	};
	__u32 key = DEFAULT_SERVICE_ID;
	if (!err)
		err = bpf_map_update_elem(env.nexthop_map_fd, &key, &nh, BPF_ANY);

	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);

	if (!err)
		err = expect_u32("retval", retval, XDP_DROP);

	if (!err)
		err = expect_counter(&env, DR_NEXTHOP_UNRESOLVED, 1);

	if (!err)
		err = expect_svc_stat(&env, key, 0, 0, 1, frame.len, DR_NEXTHOP_UNRESOLVED, 1);

	env_close(&env);
	return err;
}

static int test_nexthop_recovery(void)
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
		err = seed_default_enabled_service(&env);

	__u32 key = DEFAULT_SERVICE_ID;
	if (!err) {
		err = bpf_map_delete_elem(env.nexthop_map_fd, &key);
		if (err && errno == ENOENT)
			err = 0;
	}

	if (!err)
		err = run_frame_current_maps(&env, &frame, &retval);
	if (!err)
		err = expect_u32("unseeded retval", retval, XDP_DROP);
	if (!err)
		err = expect_counter(&env, DR_NEXTHOP_UNRESOLVED, 1);

	struct nexthop nh = {
		.dst_mac = {0x00, 0xaa, 0xbb, 0xcc, 0xdd, 0xee},
		.src_mac = {0x00, 0x11, 0x22, 0x33, 0x44, 0x55},
		.resolved = 1,
	};
	if (!err)
		err = bpf_map_update_elem(env.nexthop_map_fd, &key, &nh, BPF_ANY);

	__u32 dev_key = 0;
	__u32 dev_val = 1;
	if (!err)
		err = bpf_map_update_elem(env.tx_devmap_fd, &dev_key, &dev_val, BPF_ANY);

	uint8_t data_out[512] = {};
	struct bpf_test_run_opts opts = {
		.sz = sizeof(opts),
		.data_in = frame.data,
		.data_size_in = frame.len,
		.data_out = data_out,
		.data_size_out = sizeof(data_out),
		.repeat = 1,
	};

	if (!err) {
		err = bpf_prog_test_run_opts(env.prog_fd, &opts);
		if (err) {
			fprintf(stderr, "BPF_PROG_TEST_RUN failed: %s\n", strerror(errno));
			err = -1;
		} else {
			retval = opts.retval;
		}
	}

	if (!err)
		err = expect_u32("seeded retval", retval, XDP_REDIRECT);

	if (!err) {
		struct ethhdr *eth = (struct ethhdr *)data_out;
		if (memcmp(eth->h_dest, nh.dst_mac, ETH_ALEN) != 0 ||
		    memcmp(eth->h_source, nh.src_mac, ETH_ALEN) != 0) {
			fprintf(stderr, "error: L2 rewrite recovery mismatch\n");
			err = -1;
		}
	}

	env_close(&env);
	return err;
}

static int test_nexthop_bypass_verbatim(void)
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
		err = set_node_bypass(&env, 1);

	__u32 dev_key = 0;
	__u32 dev_val = 1;
	if (!err)
		err = bpf_map_update_elem(env.tx_devmap_fd, &dev_key, &dev_val, BPF_ANY);

	uint8_t data_out[512] = {};
	struct bpf_test_run_opts opts = {
		.sz = sizeof(opts),
		.data_in = frame.data,
		.data_size_in = frame.len,
		.data_out = data_out,
		.data_size_out = sizeof(data_out),
		.repeat = 1,
	};

	if (!err) {
		err = bpf_prog_test_run_opts(env.prog_fd, &opts);
		if (err) {
			fprintf(stderr, "BPF_PROG_TEST_RUN failed: %s\n", strerror(errno));
			err = -1;
		} else {
			retval = opts.retval;
		}
	}

	if (!err)
		err = expect_u32("bypass retval", retval, XDP_REDIRECT);

	if (!err) {
		struct ethhdr *eth_in = (struct ethhdr *)frame.data;
		struct ethhdr *eth_out = (struct ethhdr *)data_out;
		if (memcmp(eth_in->h_dest, eth_out->h_dest, ETH_ALEN) != 0 ||
		    memcmp(eth_in->h_source, eth_out->h_source, ETH_ALEN) != 0) {
			fprintf(stderr, "error: L2 bypass rewrite changed headers\n");
			err = -1;
		}
	}

	env_close(&env);
	return err;
}

int main(void)
{
		const struct test_case tests[] = {
			{ "config maps load", test_config_maps_load },
		{ "apply fresh inner install round-trips",
		  test_apply_fresh_inner_install },
		{ "apply swap adds allow rule", test_apply_swap_adds_allow_rule },
		{ "apply swap adds service and preserves existing",
		  test_apply_swap_adds_service_and_preserves_existing },
		{ "apply swap disables service", test_apply_swap_disables_service },
		{ "apply swap removes service", test_apply_swap_removes_service },
		{ "apply swap carries global blacklist",
		  test_apply_swap_carries_global_blacklist },
		{ "apply verify rejects structural mismatches",
		  test_apply_verify_rejects_structural_mismatches },
		{ "apply forced failures preserve live slot",
		  test_apply_forced_failures_preserve_live_slot },
		{ "apply interruption before commit preserves live slot",
		  test_apply_interruption_before_commit_preserves_live_slot },
		{ "apply same snapshot toggles slots and versions",
		  test_apply_same_snapshot_toggles_slots_and_versions },
		{ "global apply rebuilds hot path blacklist",
		  test_global_apply_rebuilds_hot_path_blacklist },
		{ "global apply carries service outers and bitmap",
		  test_global_apply_carries_service_outers_and_bitmap },
		{ "global apply expands /16 to bloom coverage",
		  test_global_apply_expands_16_to_bloom_coverage },
		{ "global apply broad prefix sets escape flag",
		  test_global_apply_broad_prefix_sets_escape_flag },
		{ "global apply empty snapshot clears active meta",
		  test_global_apply_empty_snapshot_clears_active_meta },
		{ "global apply build failure preserves live slot",
		  test_global_apply_build_failure_preserves_live_slot },
		{ "global apply verify failure preserves live slot",
		  test_global_apply_verify_failure_preserves_live_slot },
		{ "global apply alternates with service apply",
		  test_global_apply_alternates_with_service_apply },
			{ "fair committed spin lock mutates tokens",
			  test_fair_committed_spin_lock_mutates_tokens },
			{ "ingress cap under limit continues",
			  test_ingress_cap_under_cap_continues },
			{ "ingress cap PPS exhausts independently",
			  test_ingress_cap_pps_exhausts_independently },
			{ "ingress cap BPS exhausts independently",
			  test_ingress_cap_bps_exhausts_independently },
			{ "ingress cap stops before policy stages",
			  test_ingress_cap_stops_before_policy_stages },
			{ "ingress cap precedes VIP", test_ingress_cap_precedes_vip },
			{ "ingress cap missing config fails closed",
			  test_ingress_cap_missing_config_fails_closed },
			{ "ingress cap version flip resets bucket",
			  test_ingress_cap_version_flip_resets_bucket },
			{ "fair committed exact admit count",
			  test_fair_committed_exact_admit_count },
			{ "fair burst dual draws node headroom",
			  test_fair_burst_dual_draws_node_headroom },
			{ "fair service ceiling drop", test_fair_service_ceiling_drop },
			{ "fair congestion drop keeps node reason",
			  test_fair_congestion_drop_keeps_reason_at_node },
			{ "fair zero committed uses burst only",
			  test_fair_zero_committed_uses_burst_only },
			{ "fair committed equals ceiling has no burst",
			  test_fair_committed_equals_ceiling_has_no_burst },
			{ "fair version flip regrants burst once",
			  test_fair_version_flip_regrants_burst_once },
			{ "fair zero node headroom sheds all burst",
			  test_fair_zero_node_headroom_sheds_all_burst },
			{ "fairness cap isolates committed neighbor",
			  test_fairness_cap_isolates_committed_neighbor },
			{ "fairness ceiling isolates committed neighbor",
			  test_fairness_ceiling_isolates_committed_neighbor },
			{ "fairness congestion isolates committed neighbor",
			  test_fairness_congestion_isolates_committed_neighbor },
			{ "whitelist bloom round trip",
			  test_whitelist_bloom_round_trip },
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
		{ "bypass service miss redirects and counts",
		  test_bypass_service_miss_redirects_and_counts },
		{ "bypass preserves parse drops", test_bypass_preserves_parse_drops },
		{ "bypass off uses normal service lookup",
		  test_bypass_off_uses_normal_service_lookup },
		{ "service disabled drops", test_service_disabled_drops },
			{ "enabled service sets redirect meta",
			  test_enabled_service_sets_redirect_meta },
			{ "service stat clean counts exact",
			  test_svc_stat_clean_counts_exact },
			{ "service stat drop counts exact",
			  test_svc_stat_drop_counts_exact },
			{ "whitelist hit bypasses rules",
			  test_whitelist_hit_bypasses_rules },
			{ "whitelist scope does not cross service",
			  test_whitelist_scope_does_not_cross_service },
			{ "whitelist out of range takes rule path",
			  test_whitelist_out_of_range_takes_rule_path },
			{ "whitelist bloom false positive clean miss",
			  test_whitelist_bloom_false_positive_clean_miss },
			{ "whitelist broad entry skips bloom and hits",
			  test_whitelist_broad_entry_skips_bloom_and_hits },
			{ "whitelist inactive flag treats entries as clean miss",
			  test_whitelist_inactive_flag_treats_entries_as_clean_miss },
			{ "whitelist vip config without set flags misses",
			  test_whitelist_vip_config_without_set_flags_misses },
			{ "whitelist missing vip config fails closed",
			  test_whitelist_missing_vip_config_fails_closed },
			{ "whitelist missing LPM inner fails closed",
			  test_whitelist_missing_lpm_inner_fails_closed },
			{ "whitelist disabled service precedes stage",
			  test_whitelist_disabled_service_precedes_stage },
			{ "whitelist GRE hit redirects protocol blind",
			  test_whitelist_gre_hit_redirects_protocol_blind },
			{ "blacklist amp port 53 drops",
			  test_blacklist_amp_port_53_drops },
			{ "blacklist amp port 11211 drops",
			  test_blacklist_amp_port_11211_drops },
			{ "blacklist TCP source 53 passes port filter",
			  test_blacklist_tcp_source_53_passes_port_filter },
			{ "blacklist bogon RFC1918 drops",
			  test_blacklist_bogon_rfc1918_drops },
			{ "blacklist bogon loopback drops",
			  test_blacklist_bogon_loopback_drops },
			{ "blacklist bogon multicast drops",
			  test_blacklist_bogon_multicast_drops },
			{ "blacklist bogon TEST-NET drops",
			  test_blacklist_bogon_test_net_drops },
			{ "blacklist bitmap hit adjacent and empty pass",
			  test_blacklist_bitmap_hit_adjacent_and_empty_pass },
			{ "blacklist amp precedes bogon",
			  test_blacklist_amp_precedes_bogon },
			{ "blacklist bogon precedes bitmap",
			  test_blacklist_bogon_precedes_bitmap },
			{ "blacklist whitelist bypasses amp and bogon",
			  test_blacklist_whitelist_bypasses_amp_and_bogon },
			{ "blacklist ICMP skips port filters",
			  test_blacklist_icmp_skips_port_filters },
			{ "blacklist missing bitmap inner fails closed",
			  test_blacklist_missing_bitmap_inner_fails_closed },
			{ "blacklist global hit drops two services",
			  test_blacklist_global_hit_drops_two_services },
			{ "blacklist service scoped hit does not cross service",
			  test_blacklist_service_scoped_hit_does_not_cross_service },
			{ "blacklist clean miss reaches rules with active global",
			  test_blacklist_clean_miss_reaches_rules_with_active_global },
			{ "blacklist whitelist over global blacklist",
			  test_blacklist_whitelist_over_global_blacklist },
			{ "blacklist global precedes service attribution",
			  test_blacklist_global_precedes_service_attribution },
			{ "blacklist global bloom false positive counts",
			  test_blacklist_global_bloom_false_positive_counts },
			{ "blacklist service bloom false positive counts",
			  test_blacklist_service_bloom_false_positive_counts },
			{ "blacklist global broad escape hits without fp",
			  test_blacklist_global_broad_escape_hits_without_fp },
			{ "blacklist missing global LPM inner fails closed",
			  test_blacklist_missing_global_lpm_inner_fails_closed },
			{ "blacklist missing service LPM inner fails closed",
			  test_blacklist_missing_service_lpm_inner_fails_closed },
			{ "VIP ceiling PPS deterministic terminal drop",
			  test_vip_ceiling_pps_deterministic_terminal_drop },
			{ "VIP ceiling PPS zero blocks",
			  test_vip_ceiling_pps_zero_blocks },
			{ "VIP ceiling PPS drop preserves BPS tokens",
			  test_vip_ceiling_pps_drop_preserves_bps_tokens },
			{ "VIP ceiling aggregate budget across sources",
			  test_vip_ceiling_aggregate_budget_across_sources },
			{ "VIP ceiling reset on config version",
			  test_vip_ceiling_reset_on_config_version },
			{ "VIP ceiling normal mode fresh bucket admits",
			  test_vip_ceiling_normal_mode_fresh_bucket_admits },
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
		{ "nexthop redirect success", test_nexthop_redirect_success },
		{ "nexthop unresolved drop", test_nexthop_unresolved_drop },
		{ "nexthop recovery", test_nexthop_recovery },
		{ "nexthop bypass verbatim", test_nexthop_bypass_verbatim },
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
