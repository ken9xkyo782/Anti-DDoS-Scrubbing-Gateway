#!/usr/bin/env bash
set -eu

if [ "$(id -u)" -ne 0 ]; then
	printf '%s\n' "global apply smoke requires root/CAP_NET_ADMIN" >&2
	exit 1
fi

command -v ip >/dev/null
command -v python3 >/dev/null
command -v bpftool >/dev/null

LOADER=${LOADER:-./build/xdp_gateway_loader}
APPLY=${APPLY:-./build/xdpgw-apply}
DPSTAT=${DPSTAT:-./build/dpstat}
TOOLS=${TOOLS:-./tests/apply_smoke.py}
IN_IF=gplin0
SRC_IF=gplsrc0
OUT_IF=gplout0
SINK_IF=gplsink0
LOG=${TMPDIR:-/tmp}/xdp-gateway-global-apply.$$.log
SNAPSHOT=${TMPDIR:-/tmp}/xdp-gateway-global-apply.$$.bin
PASS_SRC=${TMPDIR:-/tmp}/xdp-gateway-global-apply-pass.$$.bpf.c
PASS_OBJ=${TMPDIR:-/tmp}/xdp-gateway-global-apply-pass.$$.bpf.o
PASS_PIN=/sys/fs/bpf/xdp_gateway_global_apply_pass_$$
LOADER_PID=

cleanup()
{
	if [ -n "${LOADER_PID}" ] && kill -0 "${LOADER_PID}" 2>/dev/null; then
		kill "${LOADER_PID}" 2>/dev/null || true
		wait "${LOADER_PID}" 2>/dev/null || true
	fi
	ip link del "${SRC_IF}" 2>/dev/null || true
	ip link del "${SINK_IF}" 2>/dev/null || true
	bpftool net detach xdp dev "${SINK_IF}" 2>/dev/null || true
	rm -f "${LOG}" "${SNAPSHOT}" "${PASS_SRC}" "${PASS_OBJ}" "${PASS_PIN}"
}

trap cleanup EXIT
cleanup

ip link add "${SRC_IF}" type veth peer name "${IN_IF}"
ip link add "${SINK_IF}" type veth peer name "${OUT_IF}"
for iface in "${SRC_IF}" "${IN_IF}" "${SINK_IF}" "${OUT_IF}"; do
	ip link set dev "${iface}" up
done

cat >"${PASS_SRC}" <<'C'
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

SEC("xdp")
int xdp_pass(struct xdp_md *ctx)
{
	(void)ctx;
	return XDP_PASS;
}

char _license[] SEC("license") = "GPL";
C

ARCH=$(uname -m | sed 's/x86_64/x86/' | sed 's/aarch64/arm64/')
UAPI_INCLUDE=/usr/include/$(uname -m)-linux-gnu
${BPF_CLANG:-clang} -g -O2 -target bpf -D__TARGET_ARCH_${ARCH} \
	-I"${UAPI_INCLUDE}" -c "${PASS_SRC}" -o "${PASS_OBJ}"
bpftool prog load "${PASS_OBJ}" "${PASS_PIN}" type xdp
bpftool net attach xdp pinned "${PASS_PIN}" dev "${SINK_IF}"

SERVICE_DEST=10.0.0.2 XDPGW_SEED_GBL_CIDR=45.45.0.66/32 \
	"${LOADER}" "${IN_IF}" "${OUT_IF}" >"${LOG}" 2>&1 &
LOADER_PID=$!

sleep 1
if ! kill -0 "${LOADER_PID}" 2>/dev/null; then
	cat "${LOG}" >&2 || true
	printf '%s\n' "loader exited before global apply smoke" >&2
	exit 1
fi

before=$(${DPSTAT} active_config)
before_slot=$(printf '%s\n' "${before}" | awk '$1 == "active_slot" {print $2}')
before_version=$(printf '%s\n' "${before}" | awk '$1 == "version" {print $2}')
if [ "${before_slot}" != "0" ] || [ "${before_version}" != "1" ]; then
	printf '%s\n' "${before}" >&2
	printf '%s\n' "unexpected loader active_config before global apply" >&2
	exit 1
fi

python3 "${TOOLS}" expect "${SRC_IF}" "${SINK_IF}" 45.0.0.0 10.0.0.2 deliver
python3 "${TOOLS}" generate-global-small "${SNAPSHOT}"
result=$("${APPLY}" "${SNAPSHOT}")
if [ "${result}" != '{"active_slot":1,"node_map_version":2}' ]; then
	printf 'unexpected global apply result: %s\n' "${result}" >&2
	exit 1
fi

after=$(${DPSTAT} active_config)
after_slot=$(printf '%s\n' "${after}" | awk '$1 == "active_slot" {print $2}')
after_version=$(printf '%s\n' "${after}" | awk '$1 == "version" {print $2}')
if [ "${after_slot}" != "1" ] || [ "${after_version}" != "2" ]; then
	printf '%s\n' "${after}" >&2
	printf '%s\n' "global apply did not flip active_config exactly once" >&2
	exit 1
fi

python3 "${TOOLS}" expect "${SRC_IF}" "${SINK_IF}" 45.0.0.0 10.0.0.2 drop
python3 "${TOOLS}" expect "${SRC_IF}" "${SINK_IF}" 45.45.0.66 10.0.0.2 deliver
printf '%s\n' "global apply smoke: generated feed snapshot reached blacklist_drop"
