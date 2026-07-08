#include <errno.h>
#include <linux/if_link.h>
#include <net/if.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <bpf/libbpf.h>

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

static const char *iface_from_args(int argc, char **argv)
{
	const char *ifname;

	if (argc > 1)
		return argv[1];

	ifname = getenv("IN_IFACE");
	if (ifname && ifname[0] != '\0')
		return ifname;

	return NULL;
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

int main(int argc, char **argv)
{
	const char *ifname = iface_from_args(argc, argv);
	struct xdp_gateway_bpf *skel = NULL;
	int ifindex;
	int prog_fd;
	int err;

	if (!ifname) {
		fprintf(stderr, "usage: %s <ifname>\n", argv[0]);
		fprintf(stderr, "       or set IN_IFACE=<ifname>\n");
		return 2;
	}

	ifindex = if_nametoindex(ifname);
	if (!ifindex) {
		fprintf(stderr, "failed to resolve interface %s: %s\n", ifname,
			strerror(errno));
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

	printf("loaded xdp_gateway on %s (ifindex %d)\n", ifname, ifindex);
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
