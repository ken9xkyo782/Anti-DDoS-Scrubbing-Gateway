// SPDX-License-Identifier: GPL-2.0
/*
 * bench_mc.c — Data-plane multi-core contention micro-benchmark.
 *
 * Drives xdp_gateway via BPF_PROG_TEST_RUN concurrently across N threads
 * pinned to N distinct CPUs against one service_id to measure lock/memory
 * contention scaling.
 *
 * Reuses test_parse.c compiled with TEST_PARSE_NO_MAIN.
 * Runs as root.
 */

#define _GNU_SOURCE

#pragma GCC diagnostic ignored "-Wunused-function"

#define TEST_PARSE_NO_MAIN
#include "test_parse.c"

#include <pthread.h>
#include <time.h>

#define BENCH_DEFAULT_REPEAT 500000u
#define BENCH_DEFAULT_ROUNDS 5u
#define BENCH_WARMUP_REPEAT  50000u

#define BENCH_BOGON_SRC 0x0a000001U /* 10.0.0.1 */

struct bench_worker_args {
	struct test_env *env;
	struct pkt_frame *frame;
	int cpu;
	unsigned int repeat;
	pthread_barrier_t *barrier;

	/* Outputs */
	double duration_ns;
	struct timespec t0;
	struct timespec t1;
	int err;
};

static void *bench_worker(void *arg)
{
	struct bench_worker_args *w = (struct bench_worker_args *)arg;
	struct bpf_test_run_opts opts = {
		.sz = sizeof(opts),
		.data_in = w->frame->data,
		.data_size_in = w->frame->len,
		.repeat = w->repeat,
	};

	if (pin_to_cpu(w->cpu) != 0) {
		w->err = -1;
		return NULL;
	}

	pthread_barrier_wait(w->barrier);

	clock_gettime(CLOCK_MONOTONIC, &w->t0);
	if (bpf_prog_test_run_opts(w->env->prog_fd, &opts) != 0) {
		w->err = -1;
		return NULL;
	}
	clock_gettime(CLOCK_MONOTONIC, &w->t1);

	w->duration_ns = (double)opts.duration;
	w->err = 0;
	return NULL;
}

/* Helper to snapshot per-CPU counters for CPU-spread self-check */
static int get_percpu_counts(struct test_env *env, int is_clean, __u64 *counts)
{
	int num_cpus = env->possible_cpus;
	memset(counts, 0, num_cpus * sizeof(__u64));

	if (is_clean) {
		__u32 key = DEFAULT_SERVICE_ID;
		struct svc_stat *stats = calloc(num_cpus, sizeof(*stats));
		if (!stats)
			return -1;
		if (bpf_map_lookup_elem(env->svc_stat_fd, &key, stats) == 0) {
			for (int i = 0; i < num_cpus; i++)
				counts[i] = stats[i].clean_pkts;
		}
		free(stats);
	} else {
		__u32 key = (__u32)DR_BOGON_DROP;
		if (bpf_map_lookup_elem(env->counter_fd, &key, counts) != 0)
			return -1;
	}
	return 0;
}

static int cmp_double(const void *a, const void *b)
{
	double da = *(const double *)a;
	double db = *(const double *)b;
	return (da > db) - (da < db);
}

static int bench_setup_clean_redirect(struct test_env *env, struct pkt_frame *frame)
{
	if (reset_maps(env) != 0)
		return -1;
	if (seed_default_enabled_service(env) != 0)
		return -1;
	return build_default_udp_frame(frame);
}

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

struct scenario_result {
	double ns_med;
	double ns_min;
	double ns_max;
	double mpps_agg;
	int cpus_advanced;
	int check_ok;
};

static int run_mc_scenario(struct test_env *env,
			   int (*setup_fn)(struct test_env *, struct pkt_frame *),
			   int is_clean, int N, const int *cpu_list,
			   unsigned int repeat, unsigned int rounds,
			   struct scenario_result *out)
{
	struct pkt_frame frame;
	double *round_ns_meds = calloc(rounds, sizeof(double));
	double *round_mpps = calloc(rounds, sizeof(double));
	__u64 *counts_before = calloc(env->possible_cpus, sizeof(__u64));
	__u64 *counts_after = calloc(env->possible_cpus, sizeof(__u64));
	struct bench_worker_args *args = calloc(N, sizeof(*args));
	pthread_t *threads = calloc(N, sizeof(*threads));
	pthread_barrier_t barrier;
	unsigned int r;
	int i;

	if (!round_ns_meds || !round_mpps || !counts_before || !counts_after || !args || !threads) {
		fprintf(stderr, "calloc failed\n");
		free(round_ns_meds); free(round_mpps); free(counts_before);
		free(counts_after); free(args); free(threads);
		return -1;
	}

	/* Warm-up round */
	if (setup_fn(env, &frame) != 0) {
		free(round_ns_meds); free(round_mpps); free(counts_before);
		free(counts_after); free(args); free(threads);
		return -1;
	}
	{
		struct bpf_test_run_opts opts = {
			.sz = sizeof(opts),
			.data_in = frame.data,
			.data_size_in = frame.len,
			.repeat = BENCH_WARMUP_REPEAT,
		};
		bpf_prog_test_run_opts(env->prog_fd, &opts);
	}

	for (r = 0; r < rounds; r++) {
		if (setup_fn(env, &frame) != 0)
			goto fail;

		if (get_percpu_counts(env, is_clean, counts_before) != 0)
			goto fail;

		if (pthread_barrier_init(&barrier, NULL, (unsigned int)N) != 0)
			goto fail;

		for (i = 0; i < N; i++) {
			memset(&args[i], 0, sizeof(args[i]));
			args[i].env = env;
			args[i].frame = &frame;
			args[i].cpu = cpu_list[i];
			args[i].repeat = repeat;
			args[i].barrier = &barrier;

			if (pthread_create(&threads[i], NULL, bench_worker, &args[i]) != 0) {
				pthread_barrier_destroy(&barrier);
				goto fail;
			}
		}

		for (i = 0; i < N; i++) {
			pthread_join(threads[i], NULL);
			if (args[i].err != 0) {
				pthread_barrier_destroy(&barrier);
				goto fail;
			}
		}
		pthread_barrier_destroy(&barrier);

		if (get_percpu_counts(env, is_clean, counts_after) != 0)
			goto fail;

		/* CPU-spread self-check */
		int advanced = 0;
		for (i = 0; i < env->possible_cpus; i++) {
			if (counts_after[i] > counts_before[i])
				advanced++;
		}
		out->cpus_advanced = advanced;
		out->check_ok = (advanced == N);

		if (!out->check_ok) {
			fprintf(stderr, "ABORT: CPU-spread self-check failed for N=%d: %d slots advanced (expected %d)\n",
				N, advanced, N);
			goto fail;
		}

		/* Compute metrics for round r */
		double *thread_ns = calloc(N, sizeof(double));
		struct timespec t0_min = args[0].t0;
		struct timespec t1_max = args[0].t1;

		for (i = 0; i < N; i++) {
			thread_ns[i] = args[i].duration_ns;
			if (args[i].t0.tv_sec < t0_min.tv_sec ||
			    (args[i].t0.tv_sec == t0_min.tv_sec && args[i].t0.tv_nsec < t0_min.tv_nsec))
				t0_min = args[i].t0;
			if (args[i].t1.tv_sec > t1_max.tv_sec ||
			    (args[i].t1.tv_sec == t1_max.tv_sec && args[i].t1.tv_nsec > t1_max.tv_nsec))
				t1_max = args[i].t1;
		}
		qsort(thread_ns, N, sizeof(double), cmp_double);
		round_ns_meds[r] = thread_ns[N / 2];
		free(thread_ns);

		double wall_sec = (double)(t1_max.tv_sec - t0_min.tv_sec) +
				  (double)(t1_max.tv_nsec - t0_min.tv_nsec) * 1e-9;
		round_mpps[r] = (wall_sec > 0.0) ? ((double)(N * repeat) / wall_sec) / 1e6 : 0.0;
	}

	qsort(round_ns_meds, rounds, sizeof(double), cmp_double);
	qsort(round_mpps, rounds, sizeof(double), cmp_double);

	out->ns_min = round_ns_meds[0];
	out->ns_med = round_ns_meds[rounds / 2];
	out->ns_max = round_ns_meds[rounds - 1];
	out->mpps_agg = round_mpps[rounds / 2];

	free(round_ns_meds); free(round_mpps); free(counts_before);
	free(counts_after); free(args); free(threads);
	return 0;

fail:
	free(round_ns_meds); free(round_mpps); free(counts_before);
	free(counts_after); free(args); free(threads);
	return -1;
}

static void print_usage(const char *prog)
{
	fprintf(stderr, "usage: %s [repeat] [rounds] [--max-threads M] [--cpus c0,c1,...]\n", prog);
}

int main(int argc, char **argv)
{
	unsigned int repeat = BENCH_DEFAULT_REPEAT;
	unsigned int rounds = BENCH_DEFAULT_ROUNDS;
	int max_threads_override = 0;
	int *custom_cpus = NULL;
	int custom_cpu_count = 0;
	struct test_env env;
	int arg_pos = 0;
	int i;

	for (i = 1; i < argc; i++) {
		if (strcmp(argv[i], "--max-threads") == 0) {
			if (i + 1 < argc) {
				max_threads_override = atoi(argv[++i]);
			} else {
				print_usage(argv[0]);
				return 2;
			}
		} else if (strcmp(argv[i], "--cpus") == 0) {
			if (i + 1 < argc) {
				char *cpus_str = strdup(argv[++i]);
				char *tok = strtok(cpus_str, ",");
				int cap = 16;
				custom_cpus = calloc(cap, sizeof(int));
				while (tok) {
					if (custom_cpu_count >= cap) {
						cap *= 2;
						custom_cpus = realloc(custom_cpus, cap * sizeof(int));
					}
					custom_cpus[custom_cpu_count++] = atoi(tok);
					tok = strtok(NULL, ",");
				}
				free(cpus_str);
			} else {
				print_usage(argv[0]);
				return 2;
			}
		} else if (argv[i][0] != '-') {
			arg_pos++;
			if (arg_pos == 1)
				repeat = (unsigned int)strtoul(argv[i], NULL, 10);
			else if (arg_pos == 2)
				rounds = (unsigned int)strtoul(argv[i], NULL, 10);
		}
	}

	if (repeat == 0 || rounds == 0) {
		print_usage(argv[0]);
		return 2;
	}

	if (env_open(&env) != 0)
		return 1;

	int num_cpus = (custom_cpu_count > 0) ? custom_cpu_count : env.possible_cpus;
	if (max_threads_override > 0 && max_threads_override < num_cpus)
		num_cpus = max_threads_override;

	int *cpus = calloc(num_cpus, sizeof(int));
	if (custom_cpu_count > 0) {
		for (i = 0; i < num_cpus; i++)
			cpus[i] = custom_cpus[i];
	} else {
		for (i = 0; i < num_cpus; i++)
			cpus[i] = i;
	}

	/* Build N sweep array: 1, 2, 4, 8, 16 ... up to num_cpus */
	int n_sweeps_cap = 32;
	int *n_sweeps = calloc(n_sweeps_cap, sizeof(int));
	int num_sweeps = 0;
	int current_n = 1;

	while (current_n <= num_cpus) {
		n_sweeps[num_sweeps++] = current_n;
		if (current_n * 2 > num_cpus) {
			if (current_n != num_cpus)
				n_sweeps[num_sweeps++] = num_cpus;
			break;
		}
		current_n *= 2;
	}

	printf("# xdp_gateway multi-core contention benchmark\n");
	printf("# BPF_PROG_TEST_RUN, repeat=%u, rounds=%u, %d possible CPUs, max_cpus=%d\n",
	       repeat, rounds, env.possible_cpus, num_cpus);
	printf("# Note: SMT siblings sharing physical cores may cap scaling regardless of locks.\n");
	printf("# subject=clean_redirect (committed tier)  control=bogon_drop (lock-free)\n\n");

	printf("%3s  %11s  %11s  %11s  %13s  %10s  %13s  %10s  %9s  %8s\n",
	       "N", "subj_ns_med", "subj_ns_min", "subj_ns_max", "subj_Mpps_agg", "subj_eff",
	       "ctrl_Mpps_agg", "ctrl_eff", "rel_eff", "cpus_adv");
	printf("%3s  %11s  %11s  %11s  %13s  %10s  %13s  %10s  %9s  %8s\n",
	       "---", "-----------", "-----------", "-----------", "-------------", "----------",
	       "-------------", "----------", "---------", "--------");

	double subj_mpps_1 = 0.0;
	double ctrl_mpps_1 = 0.0;
	double min_ctrl_eff = 1.0;
	int min_ctrl_eff_n = 1;

	for (i = 0; i < num_sweeps; i++) {
		int N = n_sweeps[i];
		struct scenario_result subj_res = {0};
		struct scenario_result ctrl_res = {0};

		if (run_mc_scenario(&env, bench_setup_clean_redirect, 1, N, cpus, repeat, rounds, &subj_res) != 0) {
			fprintf(stderr, "Error running subject scenario for N=%d\n", N);
			env_close(&env);
			return 1;
		}

		if (run_mc_scenario(&env, bench_setup_bogon, 0, N, cpus, repeat, rounds, &ctrl_res) != 0) {
			fprintf(stderr, "Error running control scenario for N=%d\n", N);
			env_close(&env);
			return 1;
		}

		if (N == 1) {
			subj_mpps_1 = subj_res.mpps_agg;
			ctrl_mpps_1 = ctrl_res.mpps_agg;
		}

		double subj_eff = (subj_mpps_1 > 0.0) ? (subj_res.mpps_agg / (N * subj_mpps_1)) : 1.0;
		double ctrl_eff = (ctrl_mpps_1 > 0.0) ? (ctrl_res.mpps_agg / (N * ctrl_mpps_1)) : 1.0;
		double rel_eff = (ctrl_eff > 0.0) ? (subj_eff / ctrl_eff) : 1.0;

		if (ctrl_eff < min_ctrl_eff) {
			min_ctrl_eff = ctrl_eff;
			min_ctrl_eff_n = N;
		}

		printf("%3d  %11.1f  %11.1f  %11.1f  %13.2f  %10.3f  %13.2f  %10.3f  %9.3f  %2d  ok\n",
		       N, subj_res.ns_med, subj_res.ns_min, subj_res.ns_max,
		       subj_res.mpps_agg, subj_eff, ctrl_res.mpps_agg, ctrl_eff, rel_eff,
		       subj_res.cpus_advanced);
	}

	if (min_ctrl_eff < 0.85) {
		printf("\n# WARNING: lock-free control efficiency dropped to %.3f at N=%d — harness/host thermal/SMT limit detected\n",
		       min_ctrl_eff, min_ctrl_eff_n);
	}

	free(cpus);
	free(n_sweeps);
	if (custom_cpus)
		free(custom_cpus);
	env_close(&env);
	return 0;
}
