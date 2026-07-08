#include <errno.h>
#include <arpa/inet.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <bpf/bpf.h>
#include <bpf/libbpf.h>

#include "drop_event.h"
#include "drop_reason.h"

#define PIN_DIR "/sys/fs/bpf/xdp_gateway"
#define COUNTER_PIN_PATH PIN_DIR "/counter_map"
#define RINGBUF_PIN_PATH PIN_DIR "/drop_ringbuf"
#define SAMPLE_CONFIG_PIN_PATH PIN_DIR "/sample_config"
#define SAMPLE_STATS_PIN_PATH PIN_DIR "/sample_stats"

static volatile sig_atomic_t exiting;

static void handle_signal(int sig)
{
	(void)sig;
	exiting = 1;
}

static void usage(const char *prog)
{
	fprintf(stderr, "usage: %s counters [-w seconds]\n", prog);
	fprintf(stderr, "       %s tail\n", prog);
	fprintf(stderr, "       %s rate <per_cpu_rate> <burst>\n", prog);
}

static int open_pinned_map(const char *path)
{
	int fd = bpf_obj_get(path);

	if (fd < 0)
		fprintf(stderr, "gateway not loaded or map not pinned: %s (%s)\n",
			path, strerror(errno));
	return fd;
}

static int parse_u64(const char *text, __u64 *value)
{
	char *end;
	unsigned long long parsed;

	errno = 0;
	parsed = strtoull(text, &end, 10);
	if (errno || end == text || *end != '\0')
		return -1;

	*value = (__u64)parsed;
	return 0;
}

static int read_percpu_u64(int fd, __u32 key, int possible_cpus, __u64 *sum)
{
	__u64 *values;
	int err;

	values = calloc(possible_cpus, sizeof(*values));
	if (!values)
		return -1;

	err = bpf_map_lookup_elem(fd, &key, values);
	if (err == 0) {
		*sum = 0;
		for (int i = 0; i < possible_cpus; i++)
			*sum += values[i];
	}

	free(values);
	return err;
}

static int print_counters_once(int counter_fd, int stats_fd, int possible_cpus)
{
	__u64 total;

	printf("idx  reason                       total\n");
	for (__u32 key = 0; key < DROP_REASON_COUNT; key++) {
		if (read_percpu_u64(counter_fd, key, possible_cpus, &total) != 0) {
			fprintf(stderr, "failed to read counter[%u]: %s\n", key,
				strerror(errno));
			return -1;
		}
		printf("%3u  %-28s %llu\n", key, drop_reason_name[key],
		       (unsigned long long)total);
	}

	printf("\n");
	for (__u32 key = 0; key < SAMPLE_STAT_MAX; key++) {
		static const char *const stat_name[SAMPLE_STAT_MAX] = {
			[SAMPLE_EMITTED] = "sample_emitted",
			[SAMPLE_SUPPRESSED] = "sample_suppressed",
			[SAMPLE_LOST] = "sample_lost",
		};

		if (read_percpu_u64(stats_fd, key, possible_cpus, &total) != 0) {
			fprintf(stderr, "failed to read sample_stats[%u]: %s\n", key,
				strerror(errno));
			return -1;
		}
		printf("%-28s %llu\n", stat_name[key], (unsigned long long)total);
	}

	return 0;
}

static int cmd_counters(int argc, char **argv)
{
	int counter_fd = -1;
	int stats_fd = -1;
	int possible_cpus;
	long watch_sec = 0;
	int err = 1;

	if (argc == 2 && strcmp(argv[0], "-w") == 0) {
		char *end;

		errno = 0;
		watch_sec = strtol(argv[1], &end, 10);
		if (errno || end == argv[1] || *end != '\0' || watch_sec <= 0) {
			fprintf(stderr, "invalid watch interval: %s\n", argv[1]);
			return 2;
		}
	} else if (argc != 0) {
		return 2;
	}

	possible_cpus = libbpf_num_possible_cpus();
	if (possible_cpus <= 0) {
		fprintf(stderr, "failed to detect possible CPUs\n");
		return 1;
	}

	counter_fd = open_pinned_map(COUNTER_PIN_PATH);
	if (counter_fd < 0)
		goto out;
	stats_fd = open_pinned_map(SAMPLE_STATS_PIN_PATH);
	if (stats_fd < 0)
		goto out;

	signal(SIGINT, handle_signal);
	signal(SIGTERM, handle_signal);

	do {
		if (watch_sec > 0)
			printf("\033[H\033[J");
		if (print_counters_once(counter_fd, stats_fd, possible_cpus) != 0)
			goto out;
		fflush(stdout);
		if (watch_sec > 0 && !exiting)
			sleep((unsigned int)watch_sec);
	} while (watch_sec > 0 && !exiting);

	err = 0;

out:
	if (counter_fd >= 0)
		close(counter_fd);
	if (stats_fd >= 0)
		close(stats_fd);
	return err;
}

static void format_ipv4(__u32 ip, char *buf, size_t len)
{
	struct in_addr addr = {
		.s_addr = ip,
	};

	if (!inet_ntop(AF_INET, &addr, buf, len))
		snprintf(buf, len, "<invalid>");
}

static int print_event(void *ctx, void *data, size_t len)
{
	const struct drop_event *event = data;
	char src[INET_ADDRSTRLEN];
	char dst[INET_ADDRSTRLEN];

	(void)ctx;
	if (len != sizeof(*event)) {
		fprintf(stderr, "unexpected drop event size: %zu\n", len);
		return 0;
	}

	format_ipv4(event->src_ip, src, sizeof(src));
	format_ipv4(event->dst_ip, dst, sizeof(dst));

	printf("%llu %-28s %s:%u -> %s:%u proto=%u service_id=%u\n",
	       (unsigned long long)event->ts_ns,
	       event->reason < DROP_REASON_COUNT ? drop_reason_name[event->reason] : "unknown",
	       src, ntohs(event->sport), dst, ntohs(event->dport),
	       event->ip_proto, event->service_id);
	fflush(stdout);
	return 0;
}

static int cmd_tail(void)
{
	struct ring_buffer *ring = NULL;
	int ring_fd;
	int err = 1;

	ring_fd = open_pinned_map(RINGBUF_PIN_PATH);
	if (ring_fd < 0)
		return 1;

	ring = ring_buffer__new(ring_fd, print_event, NULL, NULL);
	if (!ring) {
		fprintf(stderr, "failed to create ringbuf reader: %s\n", strerror(errno));
		goto out;
	}

	signal(SIGINT, handle_signal);
	signal(SIGTERM, handle_signal);
	while (!exiting) {
		int rc = ring_buffer__poll(ring, 250);

		if (rc < 0 && rc != -EINTR) {
			fprintf(stderr, "ringbuf poll failed: %s\n", strerror(-rc));
			goto out;
		}
	}

	err = 0;

out:
	ring_buffer__free(ring);
	close(ring_fd);
	return err;
}

static int cmd_rate(int argc, char **argv)
{
	struct sample_config config;
	__u32 key = 0;
	int fd;
	int err = 1;

	if (argc != 2)
		return 2;
	if (parse_u64(argv[0], &config.rate_per_sec) != 0 ||
	    parse_u64(argv[1], &config.burst) != 0) {
		fprintf(stderr, "invalid rate arguments\n");
		return 2;
	}

	fd = open_pinned_map(SAMPLE_CONFIG_PIN_PATH);
	if (fd < 0)
		return 1;

	if (bpf_map_update_elem(fd, &key, &config, BPF_ANY) != 0) {
		fprintf(stderr, "failed to update sample_config: %s\n", strerror(errno));
		goto out;
	}

	printf("sample rate set to %llu/s burst %llu per CPU\n",
	       (unsigned long long)config.rate_per_sec,
	       (unsigned long long)config.burst);
	err = 0;

out:
	close(fd);
	return err;
}

int main(int argc, char **argv)
{
	const char *prog = argv[0];
	int err;

	if (argc < 2) {
		usage(prog);
		return 2;
	}

	if (strcmp(argv[1], "counters") == 0)
		err = cmd_counters(argc - 2, argv + 2);
	else if (strcmp(argv[1], "tail") == 0)
		err = argc == 2 ? cmd_tail() : 2;
	else if (strcmp(argv[1], "rate") == 0)
		err = cmd_rate(argc - 2, argv + 2);
	else
		err = 2;

	if (err == 2)
		usage(prog);
	return err;
}
