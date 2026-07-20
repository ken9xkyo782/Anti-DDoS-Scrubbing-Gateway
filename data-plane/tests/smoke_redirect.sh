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
IN_IF=slrdin0
SRC_IF=slrdsrc0
OUT_IF=slrdout0
SINK_IF=slrdsink0
LOG=${TMPDIR:-/tmp}/xdp-gateway-smoke.$$.log
PASS_SRC=${TMPDIR:-/tmp}/xdp-gateway-pass.$$.bpf.c
PASS_OBJ=${TMPDIR:-/tmp}/xdp-gateway-pass.$$.bpf.o
PASS_PIN=/sys/fs/bpf/xdp_gateway_pass_$$
LOADER_PID=

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
	rm -f "${PASS_PIN}" "${PASS_SRC}" "${PASS_OBJ}"
	rm -f "${LOG}"
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

DPSTAT=${DPSTAT:-./build/dpstat}
SERVICE_DEST=10.0.0.2 "${LOADER}" "${IN_IF}" "${OUT_IF}" >"${LOG}" 2>&1 &
LOADER_PID=$!

sleep 1
if ! kill -0 "${LOADER_PID}" 2>/dev/null; then
	cat "${LOG}" >&2 || true
	echo "loader exited before smoke could send a frame" >&2
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
src.send(frame)

deadline = time.time() + 3
received = None
while time.time() < deadline:
    ready, _, _ = select.select([sink], [], [], max(0, deadline - time.time()))
    if not ready:
        break
    pkt = sink.recv(65535)
    if pkt[: len(frame)] == frame:
        received = pkt
        break
    if len(pkt) >= len(frame) and pkt[12:34] == frame[12:34]:
        received = pkt
        break

if received is None:
    raise SystemExit("no redirected frame received on OUT peer")

ttl_off = 14 + 8
csum_off = 14 + 10
if received[ttl_off] != frame[ttl_off]:
    raise SystemExit(
        "TTL changed: got %d want %d" % (received[ttl_off], frame[ttl_off])
    )
if received[csum_off : csum_off + 2] != frame[csum_off : csum_off + 2]:
    raise SystemExit("IPv4 checksum changed")

print("delivered: TTL/csum unchanged")
PY
then
	cat "${LOG}" >&2 || true
	exit 1
fi
