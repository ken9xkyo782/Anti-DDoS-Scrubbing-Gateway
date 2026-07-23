// SPDX-License-Identifier: GPL-2.0
/*
 * bench_dp.c — Data-plane load / throughput micro-benchmark.
 *
 * Drives the real xdp_gateway XDP program in-kernel via BPF_PROG_TEST_RUN with
 * a high `.repeat` count and reads back the kernel-reported average execution
 * time per packet (opts.duration, in ns). From ns/packet we derive the
 * single-core packet rate (Mpps) and the wire throughput at the benchmarked
 * frame size — the per-core load-bearing ceiling of each verdict path.
 *
 * This reuses the full test harness (env setup, map seeding, packet builders)
 * from test_parse.c, compiled with TEST_PARSE_NO_MAIN so its main() is elided.
 * The measured cost is pure program logic (parse + all filter stages + policy);
 * the classic (non-live) test-run path executes the program `repeat` times and
 * does NOT perform the XDP redirect side effect, which is exactly the per-packet
 * CPU cost we want to characterise.
 *
 * Runs as root. Pins to CPU 0 for stable timing.
 */

#define _GNU_SOURCE

/* Silence the many unused-helper warnings we inherit from test_parse.c: the
 * benchmark only calls a subset of its static seed/run helpers. */
#pragma GCC diagnostic ignored "-Wunused-function"

#define TEST_PARSE_NO_MAIN
#include "test_parse.c"

#include <time.h>

#define BENCH_DEFAULT_REPEAT 500000u
#define BENCH_DEFAULT_ROUNDS 7u
#define BENCH_WARMUP_REPEAT 50000u

/* A dest with no configured service (drives the service-miss path). */
#define BENCH_UNPROT_DST 0x0a000063U /* 10.0.0.99 */
/* An RFC1918 source (drives the bogon-source drop path). */
#define BENCH_BOGON_SRC 0x0a000001U /* 10.0.0.1 */

typedef int (*bench_setup_fn)(struct test_env *env, struct pkt_frame *frame);

struct bench_scenario {
	const char *name;
	const char *desc;
	bench_setup_fn setup;
};

/* ---- scenario builders -------------------------------------------------- */

/* S1: legitimate allowed traffic → full accept pipeline ending in redirect. */
static int bench_setup_clean_redirect(struct test_env *env,
				      struct pkt_frame *frame)
{
	if (reset_maps(env) != 0)
		return -1;
	if (seed_default_enabled_service(env) != 0)
		return -1;
	return build_default_udp_frame(frame);
}

/* S2: source in the global blacklist → terminal drop after blacklist lookup. */
static int bench_setup_blacklist_global(struct test_env *env,
					struct pkt_frame *frame)
{
	if (reset_maps(env) != 0)
		return -1;
	if (seed_default_enabled_service(env) != 0)
		return -1;
	/* /32 blacklist entry matching the default frame's public source. */
	if (seed_global_blacklist(env, 0, TEST_SRC_PUB_A, 32) != 0)
		return -1;
	return build_default_udp_frame(frame);
}

/* S3: RFC1918 (bogon) source → dropped by the spoof/bogon filter. */
static int bench_setup_bogon(struct test_env *env, struct pkt_frame *frame)
{
	if (reset_maps(env) != 0)
		return -1;
	if (seed_default_enabled_service(env) != 0)
		return -1;
	if (build_default_udp_frame(frame) != 0)
		return -1;
	return set_ipv4_addrs(frame, BENCH_BOGON_SRC, 0x0a000002U);
}

/* S4: UDP reflection from a configured blocked source port -> bitmap drop.
 * Amplification replies arrive with the reflector's SOURCE port; port 1024 is
 * not in the hardcoded set, so this exercises the configurable bitmap lookup. */
static int bench_setup_amp_port(struct test_env *env, struct pkt_frame *frame)
{
	if (reset_maps(env) != 0)
		return -1;
	if (seed_default_enabled_service(env) != 0)
		return -1;
	if (seed_blocked_port(env, 0, 1024) != 0)
		return -1;
	return build_udp_frame_ports(frame, 1024, 443); /* sport 1024 blocked */
}

/* S5: traffic to an unprotected destination → cheap service-miss drop. */
static int bench_setup_service_miss(struct test_env *env,
				    struct pkt_frame *frame)
{
	if (reset_maps(env) != 0)
		return -1;
	if (seed_default_enabled_service(env) != 0)
		return -1;
	if (build_default_udp_frame(frame) != 0)
		return -1;
	return set_ipv4_addrs(frame, TEST_SRC_PUB_A, BENCH_UNPROT_DST);
}

/* S6: allowed service but no matching allow-rule → default-deny (not_allowed). */
static int bench_setup_not_allowed(struct test_env *env,
				   struct pkt_frame *frame)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 1,
	};

	if (reset_maps(env) != 0)
		return -1;
	if (seed_service(env, 0, DEFAULT_DST, 32, DEFAULT_SERVICE_ID, 1) != 0)
		return -1;
	/* Allow only dport 80; the default frame targets 53 → falls through. */
	block.rules[0] = allow_rule(IPPROTO_UDP, 0, UINT16_MAX, 80, 80,
				    RULE_F_ENABLED);
	if (seed_rule_block(env, 0, DEFAULT_SERVICE_ID, &block) != 0)
		return -1;
	if (set_active(env, 0, 1) != 0)
		return -1;
	return build_default_udp_frame(frame); /* dport 53 */
}

/* S7: non-IPv4 (IPv6) → earliest parse-stage drop; cheapest path (lower bound). */
static int bench_setup_non_ipv4(struct test_env *env, struct pkt_frame *frame)
{
	if (reset_maps(env) != 0)
		return -1;
	pkt_frame_init(frame);
	if (build_eth(frame, ETH_P_IPV6) != 0)
		return -1;
	return build_ipv6(frame);
}

static const struct bench_scenario scenarios[] = {
	{ "clean_redirect", "allowed UDP, full accept pipeline -> redirect",
	  bench_setup_clean_redirect },
	{ "blacklist_drop", "source in global blacklist -> drop",
	  bench_setup_blacklist_global },
	{ "bogon_drop", "RFC1918 spoofed source -> drop",
	  bench_setup_bogon },
	{ "amp_port_drop", "UDP amplification (blocked src-port bitmap) -> drop",
	  bench_setup_amp_port },
	{ "service_miss", "traffic to unprotected dest -> drop",
	  bench_setup_service_miss },
	{ "not_allowed", "no matching allow-rule (default deny) -> drop",
	  bench_setup_not_allowed },
	{ "non_ipv4_parse", "IPv6 frame, earliest parse drop (lower bound)",
	  bench_setup_non_ipv4 },
};

/* ---- measurement -------------------------------------------------------- */

static const char *verdict_str(__u32 retval)
{
	switch (retval) {
	case XDP_ABORTED:
		return "ABORTED";
	case XDP_DROP:
		return "DROP";
	case XDP_PASS:
		return "PASS";
	case XDP_TX:
		return "TX";
	case XDP_REDIRECT:
		return "REDIRECT";
	default:
		return "?";
	}
}

static int cmp_double(const void *a, const void *b)
{
	double da = *(const double *)a;
	double db = *(const double *)b;

	return (da > db) - (da < db);
}

struct bench_result {
	double ns_min;
	double ns_med;
	double ns_mean;
	__u32 retval;
	__u8 verdict;   /* pkt_meta.verdict: authoritative DP decision */
	size_t frame_len;
};

/* Authoritative data-plane decision. The classic test-run path never performs
 * the XDP redirect side effect, so the accept path's raw retval is the empty
 * devmap's XDP_DROP fallback — meaningless here. meta.verdict is what the
 * program actually decided: only the accept path sets PKT_VERDICT_REDIRECT. */
static const char *decision_str(__u8 verdict)
{
	return verdict == PKT_VERDICT_REDIRECT ? "ADMIT" : "DROP";
}

static int run_scenario(struct test_env *env,
			const struct bench_scenario *sc,
			unsigned int repeat, unsigned int rounds,
			struct bench_result *out)
{
	struct pkt_frame frame;
	double *samples;
	double sum = 0.0;
	__u32 retval = 0;
	unsigned int i;

	samples = calloc(rounds, sizeof(*samples));
	if (!samples)
		return -1;

	/* Warm-up: fault in maps/pages and stabilise the icache. */
	if (sc->setup(env, &frame) != 0) {
		fprintf(stderr, "%s: warmup setup failed\n", sc->name);
		free(samples);
		return -1;
	}
	{
		struct bpf_test_run_opts opts = {
			.sz = sizeof(opts),
			.data_in = frame.data,
			.data_size_in = frame.len,
			.repeat = BENCH_WARMUP_REPEAT,
		};
		if (bpf_prog_test_run_opts(env->prog_fd, &opts) != 0) {
			fprintf(stderr, "%s: warmup run failed: %s\n",
				sc->name, strerror(errno));
			free(samples);
			return -1;
		}
	}

	for (i = 0; i < rounds; i++) {
		struct bpf_test_run_opts opts = {
			.sz = sizeof(opts),
			.repeat = repeat,
		};

		/* Fresh map state each round: independent, no bucket drift. */
		if (sc->setup(env, &frame) != 0) {
			fprintf(stderr, "%s: setup failed\n", sc->name);
			free(samples);
			return -1;
		}
		opts.data_in = frame.data;
		opts.data_size_in = frame.len;

		if (bpf_prog_test_run_opts(env->prog_fd, &opts) != 0) {
			fprintf(stderr, "%s: run failed: %s\n", sc->name,
				strerror(errno));
			free(samples);
			return -1;
		}

		samples[i] = (double)opts.duration; /* avg ns / packet */
		sum += samples[i];
		retval = opts.retval;
		out->frame_len = frame.len;
	}

	{
		struct pkt_meta meta = {0};

		if (read_meta(env, &meta) != 0) {
			fprintf(stderr, "%s: read_meta failed: %s\n",
				sc->name, strerror(errno));
			free(samples);
			return -1;
		}
		out->verdict = meta.verdict;
	}

	qsort(samples, rounds, sizeof(*samples), cmp_double);
	out->ns_min = samples[0];
	out->ns_med = samples[rounds / 2];
	out->ns_mean = sum / rounds;
	out->retval = retval;

	free(samples);
	return 0;
}

static void print_row(const struct bench_scenario *sc,
		      const struct bench_result *r)
{
	double mpps = r->ns_med > 0.0 ? 1000.0 / r->ns_med : 0.0;
	/* Wire throughput at the benchmarked L2 frame size (+ 4B FCS,
	 * 8B preamble/SFD, 12B IFG = 24B of on-wire overhead per frame). */
	double wire_bits = (double)(r->frame_len + 24) * 8.0;
	double gbps = mpps * wire_bits / 1000.0; /* Mpps * bits/1e3 = Gbit/s */

	printf("%-16s %-8s %5zu %9.1f %9.1f %9.1f %10.2f %9.2f  %s\n",
	       sc->name, decision_str(r->verdict), r->frame_len,
	       r->ns_med, r->ns_min, r->ns_mean, mpps, gbps, sc->desc);
}

int main(int argc, char **argv)
{
	unsigned int repeat = BENCH_DEFAULT_REPEAT;
	unsigned int rounds = BENCH_DEFAULT_ROUNDS;
	struct test_env env;
	size_t i, n;

	if (argc > 1)
		repeat = (unsigned int)strtoul(argv[1], NULL, 10);
	if (argc > 2)
		rounds = (unsigned int)strtoul(argv[2], NULL, 10);
	if (repeat == 0 || rounds == 0) {
		fprintf(stderr, "usage: %s [repeat] [rounds]\n", argv[0]);
		return 2;
	}

	if (pin_to_cpu0() != 0)
		return 1;

	if (env_open(&env) != 0)
		return 1;

	n = sizeof(scenarios) / sizeof(scenarios[0]);

	printf("# xdp_gateway data-plane load benchmark\n");
	printf("# BPF_PROG_TEST_RUN, repeat=%u, rounds=%u, pinned CPU0, %d possible CPUs\n",
	       repeat, rounds, env.possible_cpus);
	printf("# ns = kernel avg execution time per packet; Mpps/Gbps = per-core ceiling\n");
	printf("# Gbps includes 24B/frame on-wire overhead (preamble+FCS+IFG)\n\n");
	printf("%-16s %-8s %5s %9s %9s %9s %10s %9s  %s\n",
	       "scenario", "decision", "bytes", "ns_med", "ns_min", "ns_mean",
	       "Mpps", "Gbps", "description");
	printf("%-16s %-8s %5s %9s %9s %9s %10s %9s  %s\n",
	       "--------", "-------", "-----", "------", "------", "------",
	       "----", "----", "-----------");

	for (i = 0; i < n; i++) {
		struct bench_result r = {0};

		if (run_scenario(&env, &scenarios[i], repeat, rounds, &r) != 0) {
			env_close(&env);
			return 1;
		}
		print_row(&scenarios[i], &r);
	}

	env_close(&env);
	return 0;
}
