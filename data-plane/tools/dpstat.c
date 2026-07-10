#include <errno.h>
#include <arpa/inet.h>
#include <limits.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include <linux/if_link.h>

#include "drop_event.h"
#include "drop_reason.h"
#include "blacklist.h"
#include "service.h"

#define PIN_DIR "/sys/fs/bpf/xdp_gateway"
#define ACTIVE_CONFIG_PIN_PATH PIN_DIR "/active_config"
#define COUNTER_PIN_PATH PIN_DIR "/counter_map"
#define RINGBUF_PIN_PATH PIN_DIR "/drop_ringbuf"
#define SAMPLE_CONFIG_PIN_PATH PIN_DIR "/sample_config"
#define SAMPLE_STATS_PIN_PATH PIN_DIR "/sample_stats"
#define BLOOM_STATS_PIN_PATH PIN_DIR "/bloom_stats"
#define SVC_STAT_PIN_PATH PIN_DIR "/svc_stat_map"

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
	fprintf(stderr, "       %s snapshot --json [--ifindex N]\n", prog);
}

static int open_pinned_map(const char *path)
{
	int fd = bpf_obj_get(path);

	if (fd < 0)
		fprintf(stderr, "gateway not loaded (offline) or map not pinned: %s (%s)\n",
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

static int read_percpu_svc_stat(int fd, __u32 key, int possible_cpus,
				struct svc_stat *sum)
{
	struct svc_stat *values;
	int err;

	values = calloc(possible_cpus, sizeof(*values));
	if (!values)
		return -1;

	err = bpf_map_lookup_elem(fd, &key, values);
	if (err == 0) {
		memset(sum, 0, sizeof(*sum));
		for (int cpu = 0; cpu < possible_cpus; cpu++) {
			sum->clean_pkts += values[cpu].clean_pkts;
			sum->clean_bytes += values[cpu].clean_bytes;
			sum->drop_pkts += values[cpu].drop_pkts;
			sum->drop_bytes += values[cpu].drop_bytes;
			for (__u32 reason = 0; reason < DROP_REASON_CAP; reason++)
				sum->drop_by_reason[reason] +=
					values[cpu].drop_by_reason[reason];
		}
	}

	free(values);
	return err;
}

struct node_snapshot {
	__u64 counters[DROP_REASON_COUNT];
	__u64 sample_stats[SAMPLE_STAT_MAX];
	__u64 bloom_stats[BLOOM_STAT_MAX];
};

struct service_snapshot {
	__u32 dp_id;
	struct svc_stat counters;
};

struct service_snapshots {
	struct service_snapshot *items;
	size_t count;
	size_t capacity;
};

static const char *const snapshot_sample_stat_name[SAMPLE_STAT_MAX] = {
	[SAMPLE_EMITTED] = "sample_emitted",
	[SAMPLE_SUPPRESSED] = "sample_suppressed",
	[SAMPLE_LOST] = "sample_lost",
};

static const char *const snapshot_bloom_stat_name[BLOOM_STAT_MAX] = {
	[BLOOM_FP_WHITELIST] = "whitelist",
	[BLOOM_FP_GLOBAL] = "global_blacklist",
	[BLOOM_FP_SERVICE] = "service_blacklist",
};

static int read_node_snapshot(int counter_fd, int stats_fd, int bloom_fd,
				  int possible_cpus, struct node_snapshot *snapshot)
{
	for (__u32 key = 0; key < DROP_REASON_COUNT; key++) {
		if (read_percpu_u64(counter_fd, key, possible_cpus,
				    &snapshot->counters[key]) != 0) {
			fprintf(stderr, "failed to read counter[%u]: %s\n", key,
				strerror(errno));
			return -1;
		}
	}

	for (__u32 key = 0; key < SAMPLE_STAT_MAX; key++) {
		if (read_percpu_u64(stats_fd, key, possible_cpus,
				    &snapshot->sample_stats[key]) != 0) {
			fprintf(stderr, "failed to read sample_stats[%u]: %s\n", key,
				strerror(errno));
			return -1;
		}
	}

	for (__u32 key = 0; key < BLOOM_STAT_MAX; key++) {
		if (read_percpu_u64(bloom_fd, key, possible_cpus,
				    &snapshot->bloom_stats[key]) != 0) {
			fprintf(stderr, "failed to read bloom_stats[%u]: %s\n", key,
				strerror(errno));
			return -1;
		}
	}

	return 0;
}

static int append_service_snapshot(struct service_snapshots *snapshots,
					   __u32 dp_id,
					   const struct svc_stat *counters)
{
	struct service_snapshot *items;
	size_t capacity;

	if (snapshots->count == snapshots->capacity) {
		capacity = snapshots->capacity ? snapshots->capacity * 2 : 8;
		items = realloc(snapshots->items, capacity * sizeof(*items));
		if (!items)
			return -1;
		snapshots->items = items;
		snapshots->capacity = capacity;
	}

	snapshots->items[snapshots->count].dp_id = dp_id;
	snapshots->items[snapshots->count].counters = *counters;
	snapshots->count++;
	return 0;
}

static int compare_service_snapshot(const void *left, const void *right)
{
	const struct service_snapshot *a = left;
	const struct service_snapshot *b = right;

	return (a->dp_id > b->dp_id) - (a->dp_id < b->dp_id);
}

static int read_service_snapshots(int fd, int possible_cpus,
				  struct service_snapshots *snapshots)
{
	__u32 key;
	__u32 next_key;
	const __u32 *current_key = NULL;
	int err;

	while ((err = bpf_map_get_next_key(fd, current_key, &next_key)) == 0) {
		struct svc_stat counters;

		if (read_percpu_svc_stat(fd, next_key, possible_cpus, &counters) != 0) {
			fprintf(stderr, "failed to read svc_stat_map[%u]: %s\n",
				next_key, strerror(errno));
			return -1;
		}
		if (append_service_snapshot(snapshots, next_key, &counters) != 0) {
			fprintf(stderr, "failed to allocate service snapshot\n");
			return -1;
		}

		key = next_key;
		current_key = &key;
	}
	if (errno != ENOENT) {
		fprintf(stderr, "failed to iterate svc_stat_map: %s\n", strerror(errno));
		return -1;
	}

	qsort(snapshots->items, snapshots->count, sizeof(*snapshots->items),
	      compare_service_snapshot);
	return 0;
}

static void print_json_string(const char *value)
{
	const unsigned char *p = (const unsigned char *)value;

	putchar('"');
	for (; *p; p++) {
		switch (*p) {
		case '"':
			fputs("\\\"", stdout);
			break;
		case '\\':
			fputs("\\\\", stdout);
			break;
		case '\b':
			fputs("\\b", stdout);
			break;
		case '\f':
			fputs("\\f", stdout);
			break;
		case '\n':
			fputs("\\n", stdout);
			break;
		case '\r':
			fputs("\\r", stdout);
			break;
		case '\t':
			fputs("\\t", stdout);
			break;
		default:
			if (*p < 0x20)
				printf("\\u%04x", *p);
			else
				putchar(*p);
		}
	}
	putchar('"');
}

static void print_named_u64_object(const char *const *names,
				   const __u64 *values, size_t count)
{
	putchar('{');
	for (size_t i = 0; i < count; i++) {
		if (i)
			putchar(',');
		print_json_string(names[i]);
		printf(":%llu", (unsigned long long)values[i]);
	}
	putchar('}');
}

static void print_service_snapshot(const struct service_snapshot *snapshot)
{
	printf("{\"dp_id\":%u,\"clean_pkts\":%llu,\"clean_bytes\":%llu,"
	       "\"drop_pkts\":%llu,\"drop_bytes\":%llu,\"drop_by_reason\":",
	       snapshot->dp_id,
	       (unsigned long long)snapshot->counters.clean_pkts,
	       (unsigned long long)snapshot->counters.clean_bytes,
	       (unsigned long long)snapshot->counters.drop_pkts,
	       (unsigned long long)snapshot->counters.drop_bytes);
	print_named_u64_object(drop_reason_name, snapshot->counters.drop_by_reason,
			       DROP_REASON_COUNT);
	putchar('}');
}

static int get_snapshot_timestamp(__u64 *timestamp_ns)
{
	struct timespec timestamp;

	if (clock_gettime(CLOCK_REALTIME, &timestamp) != 0)
		return -1;

	*timestamp_ns = (__u64)timestamp.tv_sec * 1000000000ULL + timestamp.tv_nsec;
	return 0;
}

static int query_xdp(int ifindex, const char **mode, __u32 *prog_id)
{
	struct bpf_xdp_query_opts opts = {
		.sz = sizeof(opts),
	};
	int err;

	*mode = "unknown";
	*prog_id = 0;
	if (ifindex == 0)
		return 0;

	err = bpf_xdp_query(ifindex, 0, &opts);
	if (err) {
		fprintf(stderr, "failed to query XDP on ifindex %d: %s\n", ifindex,
			strerror(-err));
		return -1;
	}

	switch (opts.attach_mode) {
	case XDP_ATTACHED_DRV:
		*mode = "native";
		*prog_id = opts.drv_prog_id;
		break;
	case XDP_ATTACHED_SKB:
		*mode = "generic";
		*prog_id = opts.skb_prog_id;
		break;
	case XDP_ATTACHED_HW:
		*prog_id = opts.hw_prog_id;
		break;
	case XDP_ATTACHED_MULTI:
		if (opts.drv_prog_id) {
			*mode = "native";
			*prog_id = opts.drv_prog_id;
		} else if (opts.skb_prog_id) {
			*mode = "generic";
			*prog_id = opts.skb_prog_id;
		} else if (opts.hw_prog_id) {
			*prog_id = opts.hw_prog_id;
		} else {
			*mode = "offline";
		}
		break;
	case XDP_ATTACHED_NONE:
		*mode = "offline";
		break;
	default:
		*prog_id = opts.prog_id;
		break;
	}

	return 0;
}

static int parse_snapshot_args(int argc, char **argv, int *ifindex)
{
	int json = 0;

	*ifindex = 0;
	for (int i = 0; i < argc; i++) {
		__u64 value;

		if (strcmp(argv[i], "--json") == 0) {
			if (json)
				return -1;
			json = 1;
			continue;
		}
		if (strcmp(argv[i], "--ifindex") != 0 || ++i == argc ||
		    parse_u64(argv[i], &value) != 0 || value == 0 ||
		    value > INT_MAX)
			return -1;
		*ifindex = (int)value;
	}

	return json ? 0 : -1;
}

static int cmd_snapshot(int argc, char **argv)
{
	struct active_config active;
	struct node_snapshot node;
	struct service_snapshots services = {};
	const char *xdp_mode;
	__u32 active_key = 0;
	__u32 xdp_prog_id;
	__u64 timestamp_ns;
	int active_fd = -1;
	int counter_fd = -1;
	int stats_fd = -1;
	int bloom_fd = -1;
	int svc_fd = -1;
	int ifindex;
	int possible_cpus;
	int err = 1;

	if (parse_snapshot_args(argc, argv, &ifindex) != 0)
		return 2;

	possible_cpus = libbpf_num_possible_cpus();
	if (possible_cpus <= 0) {
		fprintf(stderr, "failed to detect possible CPUs\n");
		goto out;
	}

	active_fd = open_pinned_map(ACTIVE_CONFIG_PIN_PATH);
	if (active_fd < 0)
		goto out;
	counter_fd = open_pinned_map(COUNTER_PIN_PATH);
	if (counter_fd < 0)
		goto out;
	stats_fd = open_pinned_map(SAMPLE_STATS_PIN_PATH);
	if (stats_fd < 0)
		goto out;
	bloom_fd = open_pinned_map(BLOOM_STATS_PIN_PATH);
	if (bloom_fd < 0)
		goto out;
	svc_fd = open_pinned_map(SVC_STAT_PIN_PATH);
	if (svc_fd < 0)
		goto out;

	if (bpf_map_lookup_elem(active_fd, &active_key, &active) != 0) {
		fprintf(stderr, "failed to read active_config[0]: %s\n", strerror(errno));
		goto out;
	}
	if (read_node_snapshot(counter_fd, stats_fd, bloom_fd, possible_cpus,
			       &node) != 0 ||
	    read_service_snapshots(svc_fd, possible_cpus, &services) != 0 ||
	    query_xdp(ifindex, &xdp_mode, &xdp_prog_id) != 0 ||
	    get_snapshot_timestamp(&timestamp_ns) != 0) {
		if (errno)
			fprintf(stderr, "failed to capture telemetry snapshot: %s\n",
				strerror(errno));
		goto out;
	}

	printf("{\"ts_ns\":%llu,\"active\":{\"slot\":%u,\"version\":%u},"
	       "\"xdp\":{\"mode\":",
	       (unsigned long long)timestamp_ns, active.active_slot, active.version);
	print_json_string(xdp_mode);
	printf(",\"prog_id\":%u,\"ifindex\":%d},\"node\":{\"counters\":",
	       xdp_prog_id, ifindex);
	print_named_u64_object(drop_reason_name, node.counters, DROP_REASON_COUNT);
	fputs(",\"sample_stats\":", stdout);
	print_named_u64_object(snapshot_sample_stat_name, node.sample_stats,
			       SAMPLE_STAT_MAX);
	fputs(",\"bloom_stats\":", stdout);
	print_named_u64_object(snapshot_bloom_stat_name, node.bloom_stats,
			       BLOOM_STAT_MAX);
	fputs("},\"services\":[", stdout);
	for (size_t i = 0; i < services.count; i++) {
		if (i)
			putchar(',');
		print_service_snapshot(&services.items[i]);
	}
	fputs("]}\n", stdout);
	err = 0;

out:
	free(services.items);
	if (active_fd >= 0)
		close(active_fd);
	if (counter_fd >= 0)
		close(counter_fd);
	if (stats_fd >= 0)
		close(stats_fd);
	if (bloom_fd >= 0)
		close(bloom_fd);
	if (svc_fd >= 0)
		close(svc_fd);
	return err;
}

static int print_counters_once(int counter_fd, int stats_fd, int bloom_fd,
			       int possible_cpus)
{
	__u64 total;
	__u64 bloom_total = 0;

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

	printf("\n");
	printf("bloom_hit_lpm_miss\n");
	for (__u32 key = 0; key < BLOOM_STAT_MAX; key++) {
		static const char *const bloom_name[BLOOM_STAT_MAX] = {
			[BLOOM_FP_WHITELIST] = "whitelist",
			[BLOOM_FP_GLOBAL] = "global_blacklist",
			[BLOOM_FP_SERVICE] = "service_blacklist",
		};

		if (read_percpu_u64(bloom_fd, key, possible_cpus, &total) != 0) {
			fprintf(stderr, "failed to read bloom_stats[%u]: %s\n",
				key, strerror(errno));
			return -1;
		}
		bloom_total += total;
		printf("%-28s %llu\n", bloom_name[key],
		       (unsigned long long)total);
	}
	printf("%-28s %llu\n", "total", (unsigned long long)bloom_total);

	return 0;
}

static int cmd_counters(int argc, char **argv)
{
	int counter_fd = -1;
	int stats_fd = -1;
	int bloom_fd = -1;
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
	bloom_fd = open_pinned_map(BLOOM_STATS_PIN_PATH);
	if (bloom_fd < 0)
		goto out;

	signal(SIGINT, handle_signal);
	signal(SIGTERM, handle_signal);

	do {
		if (watch_sec > 0)
			printf("\033[H\033[J");
		if (print_counters_once(counter_fd, stats_fd, bloom_fd,
					possible_cpus) != 0)
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
	if (bloom_fd >= 0)
		close(bloom_fd);
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
	else if (strcmp(argv[1], "snapshot") == 0)
		err = cmd_snapshot(argc - 2, argv + 2);
	else
		err = 2;

	if (err == 2)
		usage(prog);
	return err;
}
