#!/usr/bin/env bash
set -eu

if [ "$(id -u)" -ne 0 ]; then
	printf '%s\n' "applybulk requires root/CAP_NET_ADMIN" >&2
	exit 1
fi

command -v ip >/dev/null
command -v python3 >/dev/null
command -v bpftool >/dev/null

LOADER=${LOADER:-./build/xdp_gateway_loader}
APPLY=${APPLY:-./build/xdpgw-apply}
DPSTAT=${DPSTAT:-./build/dpstat}
TOOLS=${TOOLS:-./tests/apply_smoke.py}
IN_IF=ablin0
SRC_IF=ablsrc0
OUT_IF=ablout0
SINK_IF=ablsink0
LOG=${TMPDIR:-/tmp}/xdp-gateway-applybulk.$$.log
SNAPSHOT=${TMPDIR:-/tmp}/xdp-gateway-applybulk.$$.bin
LOADER_PID=

cleanup()
{
	if [ -n "${LOADER_PID}" ] && kill -0 "${LOADER_PID}" 2>/dev/null; then
		kill "${LOADER_PID}" 2>/dev/null || true
		wait "${LOADER_PID}" 2>/dev/null || true
	fi

	ip link del "${SRC_IF}" 2>/dev/null || true
	ip link del "${SINK_IF}" 2>/dev/null || true
	rm -f "${LOG}" "${SNAPSHOT}"
}

map_inner_id()
{
	bpftool map lookup pinned "$1" key hex "$2" 00 00 00 |
		sed -n 's/.*inner_map_id: \([0-9][0-9]*\).*/\1/p'
}

trap cleanup EXIT
cleanup

ip link add "${SRC_IF}" type veth peer name "${IN_IF}"
ip link add "${SINK_IF}" type veth peer name "${OUT_IF}"
for iface in "${SRC_IF}" "${IN_IF}" "${SINK_IF}" "${OUT_IF}"; do
	ip link set dev "${iface}" up
done

SERVICE_DEST=10.0.0.2 XDPGW_SEED_GBL_CIDR=45.45.0.66/32 \
	"${LOADER}" "${IN_IF}" "${OUT_IF}" >"${LOG}" 2>&1 &
LOADER_PID=$!

sleep 1
if ! kill -0 "${LOADER_PID}" 2>/dev/null; then
	cat "${LOG}" >&2 || true
	printf '%s\n' "loader exited before applybulk" >&2
	exit 1
fi

before=$(${DPSTAT} active_config)
before_slot=$(printf '%s\n' "${before}" | awk '$1 == "active_slot" {print $2}')
before_version=$(printf '%s\n' "${before}" | awk '$1 == "version" {print $2}')
if [ "${before_slot}" != "0" ] || [ "${before_version}" != "1" ]; then
	printf '%s\n' "${before}" >&2
	printf '%s\n' "unexpected loader active_config before applybulk" >&2
	exit 1
fi

for map in global_blacklist_bloom global_blacklist_lpm udp_blocked_port_bitmap; do
	before_name="before_${map}"
	before_id=$(map_inner_id "/sys/fs/bpf/xdp_gateway/${map}" 00)
	if [ -z "${before_id}" ]; then
		printf 'failed to read active %s inner id\n' "${map}" >&2
		exit 1
	fi
	printf -v "${before_name}" '%s' "${before_id}"
done

python3 "${TOOLS}" generate-bulk "${SNAPSHOT}" 1000
started_ns=$(date +%s%N)
"${APPLY}" "${SNAPSHOT}"
elapsed_ms=$(( ($(date +%s%N) - started_ns) / 1000000 ))
if [ "${elapsed_ms}" -ge 5000 ]; then
	printf 'applybulk took %sms, want <5000ms\n' "${elapsed_ms}" >&2
	exit 1
fi

after=$(${DPSTAT} active_config)
after_slot=$(printf '%s\n' "${after}" | awk '$1 == "active_slot" {print $2}')
after_version=$(printf '%s\n' "${after}" | awk '$1 == "version" {print $2}')
if [ "${after_slot}" != "1" ] || [ "${after_version}" != "2" ]; then
	printf '%s\n' "${after}" >&2
	printf '%s\n' "applybulk did not flip active_config exactly once" >&2
	exit 1
fi

for map in global_blacklist_bloom global_blacklist_lpm udp_blocked_port_bitmap; do
	before_name="before_${map}"
	after_id=$(map_inner_id "/sys/fs/bpf/xdp_gateway/${map}" 01)
	if [ "${!before_name}" != "${after_id}" ]; then
		printf '%s was rebuilt instead of carried forward\n' "${map}" >&2
		exit 1
	fi
done

printf 'applybulk: 1000 services build+verify+flip in %sms; feed inner ids carried forward\n' \
	"${elapsed_ms}"
