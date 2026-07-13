#!/usr/bin/env bash
set -eu

if [ "$(id -u)" -ne 0 ]; then
	printf '%s\n' "global apply scale requires root/CAP_NET_ADMIN" >&2
	exit 1
fi

command -v ip >/dev/null
command -v python3 >/dev/null

LOADER=${LOADER:-./build/xdp_gateway_loader}
APPLY=${APPLY:-./build/xdpgw-apply}
DPSTAT=${DPSTAT:-./build/dpstat}
TOOLS=${TOOLS:-./tests/apply_smoke.py}
IN_IF=gslin0
SRC_IF=gslsrc0
OUT_IF=gslout0
SINK_IF=gslsink0
LOG=${TMPDIR:-/tmp}/xdp-gateway-global-scale.$$.log
SNAPSHOT=${TMPDIR:-/tmp}/xdp-gateway-global-scale.$$.bin
TOO_MANY=${TMPDIR:-/tmp}/xdp-gateway-global-too-many.$$.bin
LOADER_PID=

cleanup()
{
	if [ -n "${LOADER_PID}" ] && kill -0 "${LOADER_PID}" 2>/dev/null; then
		kill "${LOADER_PID}" 2>/dev/null || true
		wait "${LOADER_PID}" 2>/dev/null || true
	fi
	ip link del "${SRC_IF}" 2>/dev/null || true
	ip link del "${SINK_IF}" 2>/dev/null || true
	rm -f "${LOG}" "${SNAPSHOT}" "${TOO_MANY}"
}

memory_current()
{
	cat /sys/fs/cgroup/memory.current 2>/dev/null || printf '%s\n' -1
}

trap cleanup EXIT
cleanup

ip link add "${SRC_IF}" type veth peer name "${IN_IF}"
ip link add "${SINK_IF}" type veth peer name "${OUT_IF}"
for iface in "${SRC_IF}" "${IN_IF}" "${SINK_IF}" "${OUT_IF}"; do
	ip link set dev "${iface}" up
done

SERVICE_DEST=10.0.0.2 "${LOADER}" "${IN_IF}" "${OUT_IF}" >"${LOG}" 2>&1 &
LOADER_PID=$!

sleep 1
if ! kill -0 "${LOADER_PID}" 2>/dev/null; then
	cat "${LOG}" >&2 || true
	printf '%s\n' "loader exited before global scale" >&2
	exit 1
fi

before=$(${DPSTAT} active_config)
if ! printf '%s\n' "${before}" | grep -qx 'active_slot 0' ||
	! printf '%s\n' "${before}" | grep -qx 'version 1'; then
	printf '%s\n' "${before}" >&2
	printf '%s\n' "unexpected active_config before global scale" >&2
	exit 1
fi

python3 "${TOOLS}" generate-global-scale "${SNAPSHOT}" 1048576
memory_before=$(memory_current)
started_ns=$(date +%s%N)
result=$("${APPLY}" "${SNAPSHOT}")
elapsed_ms=$(( ($(date +%s%N) - started_ns) / 1000000 ))
memory_after=$(memory_current)
if [ "${result}" != '{"active_slot":1,"node_map_version":2}' ]; then
	printf 'unexpected 1M global apply result: %s\n' "${result}" >&2
	exit 1
fi

after=$(${DPSTAT} active_config)
if ! printf '%s\n' "${after}" | grep -qx 'active_slot 1' ||
	! printf '%s\n' "${after}" | grep -qx 'version 2'; then
	printf '%s\n' "${after}" >&2
	printf '%s\n' "1M global apply did not flip active_config exactly once" >&2
	exit 1
fi

python3 "${TOOLS}" generate-global-too-many "${TOO_MANY}"
if "${APPLY}" "${TOO_MANY}"; then
	printf '%s\n' "1,048,577-entry global apply unexpectedly succeeded" >&2
	exit 1
fi

after_reject=$(${DPSTAT} active_config)
if [ "${after_reject}" != "${after}" ]; then
	printf '%s\n' "${after_reject}" >&2
	printf '%s\n' "too-many global snapshot changed active_config" >&2
	exit 1
fi

if [ "${memory_before}" -ge 0 ] && [ "${memory_after}" -ge 0 ]; then
	memory_delta_kib=$(( (memory_after - memory_before) / 1024 ))
else
	memory_delta_kib=n/a
fi
printf 'global apply scale: entries=1048576 elapsed_ms=%s cgroup_delta_kib=%s; rejected=1048577 before flip\n' \
	"${elapsed_ms}" "${memory_delta_kib}"
