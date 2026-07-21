#!/usr/bin/env bash
set -eu

if [ "$(id -u)" -ne 0 ]; then
	echo "make smoke requires root/CAP_NET_ADMIN"
	exit 1
fi

command -v ip >/dev/null
command -v python3 >/dev/null
command -v bpftool >/dev/null
command -v "${BPF_CLANG:-clang}" >/dev/null

LOADER=${LOADER:-./build/xdp_gateway_loader}
DPSTAT=${DPSTAT:-./build/dpstat}
IN_IF=fairin0
SRC_IF=fairsrc0
OUT_IF=fairout0
SINK_IF=fairsink0
LOG=${TMPDIR:-/tmp}/xdp-gateway-fairness.$$.log
PASS_SRC=${TMPDIR:-/tmp}/xdp-gateway-fairness-pass.$$.bpf.c
PASS_OBJ=${TMPDIR:-/tmp}/xdp-gateway-fairness-pass.$$.bpf.o
PASS_PIN=/sys/fs/bpf/xdp_gateway_fairness_pass_$$
LOADER_PID=
FRAME_LEN=60
POSSIBLE_CPUS=$(awk -F, '{ for (i = 1; i <= NF; i++) { split($i, r, "-"); n += r[2] ? r[2] - r[1] + 1 : 1 } } END { print n }' /sys/devices/system/cpu/possible)
COMMITTED_BPS=$((FRAME_LEN * 2))
CEILING_BPS=$((COMMITTED_BPS + FRAME_LEN * 2 * POSSIBLE_CPUS))

cleanup()
{
	if [ -n "${LOADER_PID}" ] && kill -0 "${LOADER_PID}" 2>/dev/null; then
		kill "${LOADER_PID}" 2>/dev/null || true
		wait "${LOADER_PID}" 2>/dev/null || true
	fi

	ip link del "${SRC_IF}" 2>/dev/null || true
	ip link del "${IN_IF}" 2>/dev/null || true
	ip link del "${SINK_IF}" 2>/dev/null || true
	ip link del "${OUT_IF}" 2>/dev/null || true
	bpftool net detach xdp dev "${SINK_IF}" 2>/dev/null || true
	rm -rf /sys/fs/bpf/xdp_gateway 2>/dev/null || true
	rm -f "${PASS_PIN}" "${PASS_SRC}" "${PASS_OBJ}" "${LOG}"
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

SERVICE_DEST=10.0.0.2 \
XDPGW_FAIR_COMMITTED_BPS="${COMMITTED_BPS}" \
XDPGW_FAIR_CEILING_BPS="${CEILING_BPS}" \
XDPGW_NODE_CLEAN_CAPACITY_BPS="${COMMITTED_BPS}" \
XDPGW_FAIR_K=3 \
XDPGW_FAIR_REF_PKT=1 \
"${LOADER}" "${IN_IF}" "${OUT_IF}" >"${LOG}" 2>&1 &
LOADER_PID=$!

sleep 1
if ! kill -0 "${LOADER_PID}" 2>/dev/null; then
	sed -n '1,240p' "${LOG}" >&2 || true
	echo "loader exited before fairness smoke could send frames" >&2
	exit 1
fi

"${DPSTAT}" set-nexthop 1 aa:aa:aa:aa:aa:aa bb:bb:bb:bb:bb:bb

if ! python3 - "${SRC_IF}" "${SINK_IF}" <<'PY'
import select
import socket
import struct
import sys
import time

src_if = sys.argv[1]
sink_if = sys.argv[2]


def checksum(data):
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    total = (total & 0xffff) + (total >> 16)
    total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


src_ip = struct.unpack("!I", socket.inet_aton("45.45.0.1"))[0]
dst_ip = struct.unpack("!I", socket.inet_aton("10.0.0.2"))[0]
payload = b"x" * 18
udp = struct.pack("!HHHH", 1234, 53, 8 + len(payload), 0)
ip_total_len = 20 + len(udp) + len(payload)
ip_no_csum = struct.pack(
    "!BBHHHBBHII",
    0x45,
    0,
    ip_total_len,
    0x1234,
    0,
    64,
    socket.IPPROTO_UDP,
    0,
    src_ip,
    dst_ip,
)
ip = struct.pack(
    "!BBHHHBBHII",
    0x45,
    0,
    ip_total_len,
    0x1234,
    0,
    64,
    socket.IPPROTO_UDP,
    checksum(ip_no_csum),
    src_ip,
    dst_ip,
)
frame = (b"\xaa" * 6) + (b"\xbb" * 6) + struct.pack("!H", 0x0800) + ip + udp + payload

sink = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
sink.bind((sink_if, 0))
sink.setblocking(False)
while True:
    ready, _, _ = select.select([sink], [], [], 0)
    if not ready:
        break
    sink.recv(65535)

src = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
src.bind((src_if, 0))
for _ in range(16):
    src.send(frame)

deadline = time.time() + 3
redirected = 0
while time.time() < deadline:
    ready, _, _ = select.select([sink], [], [], max(0, deadline - time.time()))
    if not ready:
        break
    pkt = sink.recv(65535)
    if pkt[: len(frame)] == frame:
        redirected += 1

if redirected != 2:
    raise SystemExit("redirected %d frames, want 2 before fairness drops" % redirected)

print("redirected 2 frames before fairness drops")
PY
then
	sed -n '1,240p' "${LOG}" >&2 || true
	exit 1
fi

counters=$("${DPSTAT}" counters)
for reason in congestion_drop service_ceiling_drop ingress_cap_drop; do
	if ! printf '%s\n' "${counters}" | awk -v reason="${reason}" \
		'$2 == reason && $3 > 0 { found = 1 } END { exit !found }'; then
		printf '%s\n' "${counters}" >&2
		echo "expected live ${reason} counter to be positive" >&2
		exit 1
	fi
done

echo "fairness counters: congestion_drop, service_ceiling_drop, ingress_cap_drop"
