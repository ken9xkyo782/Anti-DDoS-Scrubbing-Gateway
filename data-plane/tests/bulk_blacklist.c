#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <inttypes.h>
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/udp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>

#include <bpf/bpf.h>
#include <bpf/libbpf.h>

#include "blacklist.h"
#include "pkt_build.h"
#include "rules.h"
#include "service.h"
#include "xdp_gateway.skel.h"

#define BULK_SERVICE_ID 1
#define BULK_DST 0x0a000002U
#define BULK_FIRST_24 0x2d000000U
#define BULK_24_COUNT 4096U
#define BULK_FIRST_32 0x2d400001U
#define BULK_MISS 0x2e000001U
#define LOOKUP_REPEATS 1000U
#define BLOOM_NONMEMBER_SAMPLES 1024U

struct bulk_env {
	struct xdp_gateway_bpf *skel;
	int prog_fd;
	int service_fd;
	int active_config_fd;
	int gbl_meta_fd;
	int gbl_lpm_fd;
	int gbl_bloom_fd;
};

static int raise_memlock(void)
{
	struct rlimit limit = {
		.rlim_cur = RLIM_INFINITY,
		.rlim_max = RLIM_INFINITY,
	};

	if (setrlimit(RLIMIT_MEMLOCK, &limit) != 0) {
		fprintf(stderr, "failed to raise RLIMIT_MEMLOCK: %s\n",
			strerror(errno));
		return -1;
	}

	return 0;
}

static long long read_u64_file(const char *path)
{
	FILE *file = fopen(path, "r");
	unsigned long long value;

	if (!file)
		return -1;
	if (fscanf(file, "%llu", &value) != 1) {
		fclose(file);
		return -1;
	}

	fclose(file);
	return (long long)value;
}

static int current_cgroup_memory_path(char *path, size_t path_len)
{
	FILE *file = fopen("/proc/self/cgroup", "r");
	char line[512];
	char *cgroup_path;
	char *newline;
	int written;

	if (!file)
		return -1;

	while (fgets(line, sizeof(line), file)) {
		cgroup_path = strstr(line, "::");
		if (!cgroup_path)
			continue;
		cgroup_path += 2;
		newline = strchr(cgroup_path, '\n');
		if (newline)
			*newline = '\0';
		if (strcmp(cgroup_path, "/") == 0)
			written = snprintf(path, path_len,
					   "/sys/fs/cgroup/memory.current");
		else
			written = snprintf(path, path_len,
					   "/sys/fs/cgroup%s/memory.current",
					   cgroup_path);
		fclose(file);
		return written > 0 && (size_t)written < path_len ? 0 : -1;
	}

	fclose(file);
	return -1;
}

static long long read_cgroup_memory_bytes(void)
{
	char path[512];

	if (current_cgroup_memory_path(path, sizeof(path)) == 0)
		return read_u64_file(path);

	return read_u64_file("/sys/fs/cgroup/memory.current");
}

static long long read_rss_kib(void)
{
	FILE *file = fopen("/proc/self/status", "r");
	char line[256];
	long long value;
	char unit[16];

	if (!file)
		return -1;

	while (fgets(line, sizeof(line), file)) {
		if (sscanf(line, "VmRSS: %lld %15s", &value, unit) == 2) {
			fclose(file);
			return value;
		}
	}

	fclose(file);
	return -1;
}

static uint64_t monotonic_ns(void)
{
	struct timespec ts;

	if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
		return 0;

	return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static __u32 ipv4_prefix_mask(__u32 prefixlen)
{
	if (prefixlen == 0)
		return 0;

	return UINT32_MAX << (32 - prefixlen);
}

static int bulk_open(struct bulk_env *env)
{
	int cpus;

	memset(env, 0, sizeof(*env));

	cpus = libbpf_num_possible_cpus();
	if (cpus <= 0) {
		fprintf(stderr, "failed to detect possible CPUs\n");
		return -1;
	}

	env->skel = xdp_gateway_bpf__open();
	if (!env->skel) {
		fprintf(stderr, "failed to open BPF skeleton: %s\n",
			strerror(errno));
		return -1;
	}

	env->skel->rodata->rl_ncpus = (__u32)cpus;
	if (xdp_gateway_bpf__load(env->skel) != 0) {
		fprintf(stderr, "failed to load BPF skeleton: %s\n",
			strerror(errno));
		xdp_gateway_bpf__destroy(env->skel);
		return -1;
	}

	env->prog_fd = bpf_program__fd(env->skel->progs.xdp_gateway);
	env->service_fd = bpf_map__fd(env->skel->maps.service_inner_0);
	env->active_config_fd = bpf_map__fd(env->skel->maps.active_config);
	env->gbl_meta_fd = bpf_map__fd(env->skel->maps.gbl_meta);
	env->gbl_lpm_fd = bpf_map__fd(env->skel->maps.global_blacklist_lpm_0);
	env->gbl_bloom_fd =
		bpf_map__fd(env->skel->maps.global_blacklist_bloom_0);

	if (env->prog_fd < 0 || env->service_fd < 0 ||
	    env->active_config_fd < 0 || env->gbl_meta_fd < 0 ||
	    env->gbl_lpm_fd < 0 || env->gbl_bloom_fd < 0) {
		fprintf(stderr, "failed to resolve required BPF fds\n");
		xdp_gateway_bpf__destroy(env->skel);
		return -1;
	}

	return 0;
}

static void bulk_close(struct bulk_env *env)
{
	xdp_gateway_bpf__destroy(env->skel);
}

static int seed_runtime(struct bulk_env *env)
{
	struct active_config active = {
		.active_slot = 0,
		.version = 1,
	};
	struct service_key svc_key = {
		.prefixlen = 32,
		.addr = htonl(BULK_DST),
	};
	struct service_val svc = {
		.service_id = BULK_SERVICE_ID,
		.enabled = 1,
	};
	struct gbl_meta meta = {
		.flags = GBL_F_ACTIVE,
	};
	__u32 slot_key = 0;

	if (bpf_map_update_elem(env->active_config_fd, &slot_key, &active,
				BPF_ANY) != 0) {
		fprintf(stderr, "failed to seed active_config: %s\n",
			strerror(errno));
		return -1;
	}

	if (bpf_map_update_elem(env->service_fd, &svc_key, &svc, BPF_ANY) != 0) {
		fprintf(stderr, "failed to seed service_inner_0: %s\n",
			strerror(errno));
		return -1;
	}

	if (bpf_map_update_elem(env->gbl_meta_fd, &slot_key, &meta,
				BPF_ANY) != 0) {
		fprintf(stderr, "failed to activate gbl_meta[0]: %s\n",
			strerror(errno));
		return -1;
	}

	return 0;
}

static int insert_global_entry(struct bulk_env *env, __u32 src_host,
			       __u32 prefixlen)
{
	__u8 present = 1;
	struct bl_lpm_key lpm_key = {
		.prefixlen = prefixlen,
		.src = htonl(src_host & ipv4_prefix_mask(prefixlen)),
	};
	__be32 bloom_key = htonl(src_host & BL_SRC24_MASK);

	if (bpf_map_update_elem(env->gbl_lpm_fd, &lpm_key, &present,
				BPF_ANY) != 0) {
		fprintf(stderr, "failed to insert LPM %u/%u: %s\n",
			src_host, prefixlen, strerror(errno));
		return -1;
	}

	if (bpf_map_update_elem(env->gbl_bloom_fd, NULL, &bloom_key,
				BPF_ANY) != 0) {
		fprintf(stderr, "failed to push bloom key for %u/%u: %s\n",
			src_host, prefixlen, strerror(errno));
		return -1;
	}

	return 0;
}

static int load_bulk_entries(struct bulk_env *env, __u32 *lpm_inserts,
			     __u32 *bloom_pushes)
{
	__u32 total = GBL_LPM_MAX_ENTRIES;
	__u32 count_32 = total - BULK_24_COUNT;
	__u32 inserted = 0;

	for (__u32 i = 0; i < BULK_24_COUNT; i++) {
		__u32 src = BULK_FIRST_24 + (i << 8);

		if (insert_global_entry(env, src, 24) != 0)
			return -1;
		inserted++;
		if ((inserted & 0x3ffffU) == 0)
			printf("inserted %u/%u entries\n", inserted, total);
	}

	for (__u32 i = 0; i < count_32; i++) {
		__u32 src = BULK_FIRST_32 + i;

		if (insert_global_entry(env, src, 32) != 0)
			return -1;
		inserted++;
		if ((inserted & 0x3ffffU) == 0)
			printf("inserted %u/%u entries\n", inserted, total);
	}

	*lpm_inserts = inserted;
	*bloom_pushes = inserted;
	return 0;
}

static int lpm_lookup(int fd, __u32 src_host, int *hit)
{
	struct bl_lpm_key key = {
		.prefixlen = 32,
		.src = htonl(src_host),
	};
	__u8 present = 0;

	if (bpf_map_lookup_elem(fd, &key, &present) == 0) {
		*hit = 1;
		return 0;
	}

	if (errno == ENOENT) {
		*hit = 0;
		return 0;
	}

	return -1;
}

static int bloom_contains(int fd, __u32 src_host, int *hit)
{
	__be32 key = htonl(src_host & BL_SRC24_MASK);

	if (bpf_map_lookup_elem(fd, NULL, &key) == 0) {
		*hit = 1;
		return 0;
	}

	if (errno == ENOENT) {
		*hit = 0;
		return 0;
	}

	return -1;
}

static int verify_lpm_samples(struct bulk_env *env)
{
	const __u32 hit_24 = BULK_FIRST_24 + 77U;
	const __u32 hit_32 = BULK_FIRST_32;
	int hit;

	if (lpm_lookup(env->gbl_lpm_fd, hit_24, &hit) != 0 || !hit) {
		fprintf(stderr, "sample /24-backed LPM hit failed\n");
		return -1;
	}

	if (lpm_lookup(env->gbl_lpm_fd, hit_32, &hit) != 0 || !hit) {
		fprintf(stderr, "sample /32 LPM hit failed\n");
		return -1;
	}

	if (lpm_lookup(env->gbl_lpm_fd, BULK_MISS, &hit) != 0 || hit) {
		fprintf(stderr, "sample LPM miss failed\n");
		return -1;
	}

	return 0;
}

static int verify_bloom_samples(struct bulk_env *env, __u32 *member_hits,
				__u32 *nonmember_misses,
				__u32 *nonmember_maybes)
{
	int hit;

	*member_hits = 0;
	*nonmember_misses = 0;
	*nonmember_maybes = 0;

	if (bloom_contains(env->gbl_bloom_fd, BULK_FIRST_24 + 77U,
			   &hit) != 0 || !hit) {
		fprintf(stderr, "sample /24-backed bloom member check failed: %s\n",
			strerror(errno));
		return -1;
	}
	(*member_hits)++;

	if (bloom_contains(env->gbl_bloom_fd, BULK_FIRST_32, &hit) != 0 ||
	    !hit) {
		fprintf(stderr, "sample /32 bloom member check failed: %s\n",
			strerror(errno));
		return -1;
	}
	(*member_hits)++;

	for (__u32 i = 0; i < BLOOM_NONMEMBER_SAMPLES; i++) {
		__u32 src = BULK_MISS + (i << 8);

		if (bloom_contains(env->gbl_bloom_fd, src, &hit) != 0) {
			fprintf(stderr, "sample bloom nonmember check failed: %s\n",
				strerror(errno));
			return -1;
		}
		if (hit)
			(*nonmember_maybes)++;
		else
			(*nonmember_misses)++;
	}

	if (*nonmember_misses < BLOOM_NONMEMBER_SAMPLES / 2) {
		fprintf(stderr,
			"unexpectedly high bloom maybe rate for nonmembers: %u/%u\n",
			*nonmember_maybes, BLOOM_NONMEMBER_SAMPLES);
		return -1;
	}

	return 0;
}

static int timed_lpm_lookup(struct bulk_env *env, double *avg_ns)
{
	__u32 src = BULK_FIRST_32;
	uint64_t start;
	uint64_t end;
	int hit;

	start = monotonic_ns();
	for (__u32 i = 0; i < LOOKUP_REPEATS; i++) {
		if (lpm_lookup(env->gbl_lpm_fd, src + (i & 0xffU), &hit) != 0 ||
		    !hit) {
			fprintf(stderr, "timed LPM lookup failed\n");
			return -1;
		}
	}
	end = monotonic_ns();

	if (start == 0 || end <= start)
		return -1;

	*avg_ns = (double)(end - start) / (double)LOOKUP_REPEATS;
	return 0;
}

static int set_frame_ipv4_addrs(struct pkt_frame *frame, __u32 src_host,
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

static int build_blacklisted_frame(struct pkt_frame *frame)
{
	pkt_frame_init(frame);
	if (build_eth(frame, ETH_P_IP) ||
	    build_ipv4(frame, IPPROTO_UDP, 0, 5) ||
	    build_udp(frame, 1234, 53))
		return -1;

	return set_frame_ipv4_addrs(frame, BULK_FIRST_32, BULK_DST);
}

static int verify_program_drop(struct bulk_env *env)
{
	struct pkt_frame frame;
	struct bpf_test_run_opts opts = {
		.sz = sizeof(opts),
	};

	if (build_blacklisted_frame(&frame) != 0) {
		fprintf(stderr, "failed to build blacklisted test frame\n");
		return -1;
	}

	opts.data_in = frame.data;
	opts.data_size_in = (__u32)frame.len;
	opts.repeat = 1;

	if (bpf_prog_test_run_opts(env->prog_fd, &opts) != 0) {
		fprintf(stderr, "BPF_PROG_TEST_RUN failed: %s\n",
			strerror(errno));
		return -1;
	}

	if (opts.retval != XDP_DROP) {
		fprintf(stderr, "expected XDP_DROP for loaded blacklist entry, got %u\n",
			opts.retval);
		return -1;
	}

	return 0;
}

int main(void)
{
	struct bulk_env env;
	long long cg_before;
	long long cg_after;
	long long rss_before;
	long long rss_after;
	__u32 lpm_inserts = 0;
	__u32 bloom_pushes = 0;
	__u32 member_hits = 0;
	__u32 nonmember_misses = 0;
	__u32 nonmember_maybes = 0;
	double avg_lookup_ns = 0.0;
	int err = 1;

	if (raise_memlock() != 0)
		return 1;
	if (bulk_open(&env) != 0)
		return 1;
	if (seed_runtime(&env) != 0)
		goto out;

	cg_before = read_cgroup_memory_bytes();
	rss_before = read_rss_kib();

	if (load_bulk_entries(&env, &lpm_inserts, &bloom_pushes) != 0)
		goto out;

	cg_after = read_cgroup_memory_bytes();
	rss_after = read_rss_kib();

	if (verify_lpm_samples(&env) != 0 ||
	    verify_bloom_samples(&env, &member_hits, &nonmember_misses,
				 &nonmember_maybes) != 0 ||
	    timed_lpm_lookup(&env, &avg_lookup_ns) != 0 ||
	    verify_program_drop(&env) != 0)
		goto out;

	printf("bulk blacklist: entries=%u lpm_inserts=%u bloom_pushes=%u key_value_bytes=%zu\n",
	       GBL_LPM_MAX_ENTRIES, lpm_inserts, bloom_pushes,
	       ((sizeof(struct bl_lpm_key) + sizeof(__u8)) *
		(size_t)lpm_inserts) +
		       (sizeof(__be32) * (size_t)bloom_pushes));
	if (cg_before >= 0 && cg_after >= 0)
		printf("footprint: cgroup_delta_kib=%lld",
		       (cg_after - cg_before) / 1024);
	else
		printf("footprint: cgroup_delta_kib=n/a");
	if (rss_before >= 0 && rss_after >= 0)
		printf(" rss_delta_kib=%lld\n", rss_after - rss_before);
	else
		printf(" rss_delta_kib=n/a\n");
	printf("lookup: lpm_hit24=ok lpm_hit32=ok lpm_miss=ok avg_ns=%.1f\n",
	       avg_lookup_ns);
	printf("bloom: member_hits=%u nonmember_misses=%u/%u nonmember_maybes=%u\n",
	       member_hits, nonmember_misses, BLOOM_NONMEMBER_SAMPLES,
	       nonmember_maybes);
	printf("verdict: blacklisted source -> XDP_DROP\n");

	err = 0;

out:
	bulk_close(&env);
	return err;
}
