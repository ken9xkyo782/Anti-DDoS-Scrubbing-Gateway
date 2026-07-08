#include <errno.h>
#include <arpa/inet.h>
#include <linux/if_link.h>
#include <net/if.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <bpf/bpf.h>
#include <bpf/libbpf.h>

#include "service.h"
#include "xdp_gateway.skel.h"

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
	return 0;
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

	skel = xdp_gateway_bpf__open_and_load();
	if (!skel) {
		fprintf(stderr, "failed to open/load BPF skeleton: %s\n",
			strerror(errno));
		return 1;
	}

	prog_fd = bpf_program__fd(skel->progs.xdp_gateway);
	if (prog_fd < 0) {
		fprintf(stderr, "failed to get XDP program fd\n");
		err = 1;
		goto cleanup;
	}

	if (populate_tx_devmap(skel, out_ifindex, out_ifname) != 0 ||
	    seed_active_config(skel) != 0 ||
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
	xdp_gateway_bpf__destroy(skel);
	return err;
}
