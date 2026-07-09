#include <errno.h>
#include <arpa/inet.h>
#include <linux/if_link.h>
#include <net/if.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <bpf/bpf.h>
#include <bpf/libbpf.h>

#include "drop_event.h"
#include "rules.h"
#include "service.h"
#include "xdp_gateway.skel.h"

#define PIN_DIR "/sys/fs/bpf/xdp_gateway"
#define COUNTER_PIN_PATH PIN_DIR "/counter_map"
#define RINGBUF_PIN_PATH PIN_DIR "/drop_ringbuf"
#define SAMPLE_CONFIG_PIN_PATH PIN_DIR "/sample_config"
#define SAMPLE_STATS_PIN_PATH PIN_DIR "/sample_stats"
#define DEFAULT_SAMPLE_RATE_PER_SEC 256
#define DEFAULT_SAMPLE_BURST 64

static volatile sig_atomic_t exiting;

static void handle_signal(int sig)
{
	(void)sig;
	exiting = 1;
}

static const char *mode_name(__u8 mode)
{
	switch (mode) {
	case XDP_ATTACHED_NONE:
		return "none";
	case XDP_ATTACHED_DRV:
		return "native/DRV";
	case XDP_ATTACHED_SKB:
		return "generic/SKB";
	case XDP_ATTACHED_HW:
		return "hardware/HW";
	case XDP_ATTACHED_MULTI:
		return "multi";
	default:
		return "unknown";
	}
}

static const char *arg_or_env(int argc, char **argv, int index,
			      const char *env_name)
{
	const char *ifname;

	if (argc > index)
		return argv[index];

	ifname = getenv(env_name);
	if (ifname && ifname[0] != '\0')
		return ifname;

	return NULL;
}

static void print_usage(const char *prog)
{
	fprintf(stderr, "usage: %s <IN> <OUT>\n", prog);
	fprintf(stderr, "       or set IN_IFACE=<IN> OUT_IFACE=<OUT>\n");
	fprintf(stderr, "       optional: SERVICE_DEST=<ipv4-or-cidr>\n");
}

static int create_pin_dir(void)
{
	if (mkdir(PIN_DIR, 0700) == 0)
		return 0;

	if (errno == EEXIST)
		fprintf(stderr, "pin directory %s already exists; remove stale pins before loading\n",
			PIN_DIR);
	else
		fprintf(stderr, "failed to create pin directory %s: %s\n", PIN_DIR,
			strerror(errno));
	return -1;
}

static void remove_pin_dir(void)
{
	if (rmdir(PIN_DIR) != 0 && errno != ENOENT)
		fprintf(stderr, "warning: failed to remove pin directory %s: %s\n",
			PIN_DIR, strerror(errno));
}

static int set_pin_path(struct bpf_map *map, const char *path)
{
	int err = bpf_map__set_pin_path(map, path);

	if (err) {
		fprintf(stderr, "failed to set pin path %s: %s\n", path,
			strerror(-err));
		return -1;
	}
	return 0;
}

static int set_observability_pin_paths(struct xdp_gateway_bpf *skel)
{
	if (set_pin_path(skel->maps.counter_map, COUNTER_PIN_PATH) != 0 ||
	    set_pin_path(skel->maps.drop_ringbuf, RINGBUF_PIN_PATH) != 0 ||
	    set_pin_path(skel->maps.sample_config, SAMPLE_CONFIG_PIN_PATH) != 0 ||
	    set_pin_path(skel->maps.sample_stats, SAMPLE_STATS_PIN_PATH) != 0)
		return -1;

	return 0;
}

static int pin_map(struct bpf_map *map, const char *name)
{
	int err = bpf_map__pin(map, NULL);

	if (err) {
		fprintf(stderr, "failed to pin %s: %s\n", name, strerror(-err));
		return -1;
	}
	return 0;
}

static void unpin_map(struct bpf_map *map, const char *name)
{
	int err = bpf_map__unpin(map, NULL);

	if (err)
		fprintf(stderr, "warning: failed to unpin %s: %s\n", name,
			strerror(-err));
}

static void unpin_observability_maps(struct xdp_gateway_bpf *skel)
{
	unpin_map(skel->maps.counter_map, "counter_map");
	unpin_map(skel->maps.drop_ringbuf, "drop_ringbuf");
	unpin_map(skel->maps.sample_config, "sample_config");
	unpin_map(skel->maps.sample_stats, "sample_stats");
}

static int pin_observability_maps(struct xdp_gateway_bpf *skel)
{
	if (pin_map(skel->maps.counter_map, "counter_map") != 0)
		return -1;
	if (pin_map(skel->maps.drop_ringbuf, "drop_ringbuf") != 0)
		goto rollback;
	if (pin_map(skel->maps.sample_config, "sample_config") != 0)
		goto rollback;
	if (pin_map(skel->maps.sample_stats, "sample_stats") != 0)
		goto rollback;

	return 0;

rollback:
	unpin_observability_maps(skel);
	return -1;
}

static void report_attach_mode(int ifindex)
{
	struct bpf_xdp_query_opts opts = {
		.sz = sizeof(opts),
	};
	int err;

	err = bpf_xdp_query(ifindex, XDP_FLAGS_DRV_MODE, &opts);
	if (err) {
		fprintf(stderr, "warning: attached, but failed to query XDP mode: %s\n",
			strerror(-err));
		return;
	}

	printf("attached XDP program id %u in %s mode\n", opts.prog_id,
	       mode_name(opts.attach_mode));
}

static int parse_service_dest(const char *text, struct service_key *key)
{
	char addr_buf[INET_ADDRSTRLEN];
	struct in_addr addr;
	const char *slash;
	uint32_t host;
	uint32_t mask;
	char *end;
	long prefix = 32;
	size_t addr_len;

	slash = strchr(text, '/');
	if (slash) {
		if (strchr(slash + 1, '/'))
			return -1;

		addr_len = (size_t)(slash - text);
		if (addr_len == 0 || addr_len >= sizeof(addr_buf))
			return -1;

		memcpy(addr_buf, text, addr_len);
		addr_buf[addr_len] = '\0';

		errno = 0;
		prefix = strtol(slash + 1, &end, 10);
		if (errno || end == slash + 1 || *end != '\0' ||
		    prefix < 0 || prefix > 32)
			return -1;
	} else {
		if (strlen(text) >= sizeof(addr_buf))
			return -1;
		strcpy(addr_buf, text);
	}

	if (inet_pton(AF_INET, addr_buf, &addr) != 1)
		return -1;

	host = ntohl(addr.s_addr);
	mask = prefix == 0 ? 0 : UINT32_MAX << (32 - prefix);
	if ((host & ~mask) != 0)
		return -1;

	key->prefixlen = (__u32)prefix;
	key->addr = htonl(host & mask);
	return 0;
}

static int populate_tx_devmap(struct xdp_gateway_bpf *skel, int out_ifindex,
			      const char *out_ifname)
{
	__u32 key = 0;
	__u32 value = (__u32)out_ifindex;
	int fd = bpf_map__fd(skel->maps.tx_devmap);

	if (fd < 0 || bpf_map_update_elem(fd, &key, &value, BPF_ANY) != 0) {
		fprintf(stderr,
			"failed to populate tx_devmap[0] for OUT %s ifindex %d: %s\n",
			out_ifname, out_ifindex, strerror(errno));
		return -1;
	}

	printf("populated tx_devmap[0] with OUT %s (ifindex %d)\n",
	       out_ifname, out_ifindex);
	return 0;
}

static int seed_active_config(struct xdp_gateway_bpf *skel)
{
	struct active_config config = {
		.active_slot = 0,
		.version = 1,
	};
	__u32 key = 0;
	int fd = bpf_map__fd(skel->maps.active_config);

	if (fd < 0 || bpf_map_update_elem(fd, &key, &config, BPF_ANY) != 0) {
		fprintf(stderr, "failed to seed active_config[0]: %s\n",
			strerror(errno));
		return -1;
	}

	printf("seeded active_config[0] active_slot=0 version=1\n");
	return 0;
}

static int seed_sample_config(struct xdp_gateway_bpf *skel)
{
	struct sample_config config = {
		.rate_per_sec = DEFAULT_SAMPLE_RATE_PER_SEC,
		.burst = DEFAULT_SAMPLE_BURST,
	};
	__u32 key = 0;
	int fd = bpf_map__fd(skel->maps.sample_config);

	if (fd < 0 || bpf_map_update_elem(fd, &key, &config, BPF_ANY) != 0) {
		fprintf(stderr, "failed to seed sample_config[0]: %s\n",
			strerror(errno));
		return -1;
	}

	printf("seeded sample_config[0] rate=%u/s burst=%u per CPU\n",
	       DEFAULT_SAMPLE_RATE_PER_SEC, DEFAULT_SAMPLE_BURST);
	return 0;
}

static int configure_rodata(struct xdp_gateway_bpf *skel)
{
	int possible_cpus = libbpf_num_possible_cpus();

	if (possible_cpus <= 0) {
		fprintf(stderr, "failed to detect possible CPUs for rl_ncpus\n");
		return -1;
	}

	skel->rodata->rl_ncpus = (__u32)possible_cpus;
	printf("configured rl_ncpus=%u\n", skel->rodata->rl_ncpus);
	return 0;
}

static struct rule_entry loader_match_all_rule(void)
{
	struct rule_entry rule = {
		.src_lo = 0,
		.src_hi = UINT16_MAX,
		.dst_lo = 0,
		.dst_hi = UINT16_MAX,
		.proto = RULE_PROTO_ANY,
		.flags = RULE_F_ENABLED,
	};

	return rule;
}

static int seed_rule_block_fd(int fd, const char *name, __u32 service_id)
{
	struct rule_block block = {
		.version = 1,
		.rule_count = 1,
	};

	block.rules[0] = loader_match_all_rule();
	if (fd < 0 ||
	    bpf_map_update_elem(fd, &service_id, &block, BPF_ANY) != 0) {
		fprintf(stderr, "failed to seed %s with match-all rule: %s\n",
			name, strerror(errno));
		return -1;
	}

	return 0;
}

static int seed_match_all_rule_blocks(struct xdp_gateway_bpf *skel,
				      __u32 service_id)
{
	if (seed_rule_block_fd(bpf_map__fd(skel->maps.rule_block_0),
			       "rule_block_0", service_id) != 0 ||
	    seed_rule_block_fd(bpf_map__fd(skel->maps.rule_block_1),
			       "rule_block_1", service_id) != 0)
		return -1;

	printf("seeded rule_block_0/1 with match-all rule for service_id=%u\n",
	       service_id);
	return 0;
}

static int seed_service_from_env(struct xdp_gateway_bpf *skel)
{
	const char *service_dest = getenv("SERVICE_DEST");
	struct service_val val = {
		.service_id = 1,
		.enabled = 1,
	};
	struct service_key key = {};
	int fd;

	if (!service_dest || service_dest[0] == '\0') {
		printf("SERVICE_DEST unset; service map remains empty\n");
		return 0;
	}

	if (parse_service_dest(service_dest, &key) != 0) {
		fprintf(stderr,
			"invalid SERVICE_DEST %s (expected IPv4 or canonical CIDR)\n",
			service_dest);
		return -1;
	}

	fd = bpf_map__fd(skel->maps.service_inner_0);
	if (fd < 0 || bpf_map_update_elem(fd, &key, &val, BPF_ANY) != 0) {
		fprintf(stderr, "failed to seed service_inner_0 from SERVICE_DEST: %s\n",
			strerror(errno));
		return -1;
	}

	printf("seeded service_inner_0 with SERVICE_DEST=%s service_id=1 enabled=1\n",
	       service_dest);
	return seed_match_all_rule_blocks(skel, val.service_id);
}

int main(int argc, char **argv)
{
	const char *ifname = arg_or_env(argc, argv, 1, "IN_IFACE");
	const char *out_ifname = arg_or_env(argc, argv, 2, "OUT_IFACE");
	struct xdp_gateway_bpf *skel = NULL;
	int ifindex;
	int out_ifindex;
	int prog_fd;
	int err;
	int pin_dir_created = 0;
	int pins_created = 0;

	if (argc > 3 || !ifname || !out_ifname) {
		print_usage(argv[0]);
		return 2;
	}

	ifindex = if_nametoindex(ifname);
	if (!ifindex) {
		fprintf(stderr, "failed to resolve interface %s: %s\n", ifname,
			strerror(errno));
		return 1;
	}

	out_ifindex = if_nametoindex(out_ifname);
	if (!out_ifindex) {
		fprintf(stderr, "failed to resolve OUT interface %s: %s\n",
			out_ifname, strerror(errno));
		return 1;
	}

	if (create_pin_dir() != 0)
		return 1;
	pin_dir_created = 1;

	skel = xdp_gateway_bpf__open();
	if (!skel) {
		fprintf(stderr, "failed to open BPF skeleton: %s\n", strerror(errno));
		err = 1;
		goto cleanup;
	}

	if (set_observability_pin_paths(skel) != 0) {
		err = 1;
		goto cleanup;
	}

	if (configure_rodata(skel) != 0) {
		err = 1;
		goto cleanup;
	}

	err = xdp_gateway_bpf__load(skel);
	if (err) {
		fprintf(stderr, "failed to load BPF skeleton: %s\n", strerror(-err));
		err = 1;
		goto cleanup;
	}

	if (pin_observability_maps(skel) != 0) {
		err = 1;
		goto cleanup;
	}
	pins_created = 1;

	prog_fd = bpf_program__fd(skel->progs.xdp_gateway);
	if (prog_fd < 0) {
		fprintf(stderr, "failed to get XDP program fd\n");
		err = 1;
		goto cleanup;
	}

	if (populate_tx_devmap(skel, out_ifindex, out_ifname) != 0 ||
	    seed_active_config(skel) != 0 ||
	    seed_sample_config(skel) != 0 ||
	    seed_service_from_env(skel) != 0) {
		err = 1;
		goto cleanup;
	}

	err = bpf_xdp_attach(ifindex, prog_fd, XDP_FLAGS_DRV_MODE, NULL);
	if (err < 0) {
		fprintf(stderr,
			"native XDP unsupported or attach failed on %s: %s\n",
			ifname, strerror(-err));
		err = 1;
		goto cleanup;
	}

	signal(SIGINT, handle_signal);
	signal(SIGTERM, handle_signal);

	printf("loaded xdp_gateway on IN %s (ifindex %d), OUT %s (ifindex %d)\n",
	       ifname, ifindex, out_ifname, out_ifindex);
	report_attach_mode(ifindex);
	printf("press Ctrl-C to detach\n");

	while (!exiting)
		pause();

	printf("detaching from %s\n", ifname);
	err = bpf_xdp_detach(ifindex, XDP_FLAGS_DRV_MODE, NULL);
	if (err < 0) {
		fprintf(stderr, "failed to detach XDP program from %s: %s\n",
			ifname, strerror(-err));
		err = 1;
	} else {
		err = 0;
	}

cleanup:
	if (pins_created)
		unpin_observability_maps(skel);
	if (pin_dir_created)
		remove_pin_dir();
	if (skel)
		xdp_gateway_bpf__destroy(skel);
	return err;
}
