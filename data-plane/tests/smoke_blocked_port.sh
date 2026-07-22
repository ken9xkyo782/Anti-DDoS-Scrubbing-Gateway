#!/usr/bin/env bash
set -eu

if [ "$(id -u)" -ne 0 ]; then
	printf '%s\n' "blocked-port smoke requires root/CAP_NET_ADMIN" >&2
	exit 1
fi

command -v ip >/dev/null
command -v python3 >/dev/null
command -v bpftool >/dev/null
command -v "${BPF_CLANG:-clang}" >/dev/null

LOADER=${LOADER:-./build/xdp_gateway_loader}
APPLY=${APPLY:-./build/xdpgw-apply}
DPSTAT=${DPSTAT:-./build/dpstat}
IN_IF=blkportin0
SRC_IF=blkportsrc0
OUT_IF=blkportout0
SINK_IF=blkportsink0
LOG=${TMPDIR:-/tmp}/xdp-gateway-blkport-smoke.$$.log
PASS_SRC=${TMPDIR:-/tmp}/xdp-gateway-blkport-pass.$$.bpf.c
PASS_OBJ=${TMPDIR:-/tmp}/xdp-gateway-blkport-pass.$$.bpf.o
PASS_PIN=/sys/fs/bpf/xdp_gateway_blkport_pass_$$
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
	rm -f "${PASS_PIN}" "${PASS_SRC}" "${PASS_OBJ}" "${LOG}"
}

probe()
{
	python3 - "${SRC_IF}" "${SINK_IF}" "$1" "$2" <<'PY'
import select
import socket
import struct
import sys
import time

src_if, sink_if, src_port_str, expected = sys.argv[1:]
src_port = int(src_port_str)


def checksum(data):
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


source = struct.unpack("!I", socket.inet_aton("192.51.100.10"))[0]
destination = struct.unpack("!I", socket.inet_aton("10.0.0.99"))[0]
payload = b"blkport-smoke-payload"
udp = struct.pack("!HHHH", src_port, 80, 8 + len(payload), 0)
ip_total_len = 20 + len(udp) + len(payload)
ip_without_checksum = struct.pack(
    "!BBHHHBBHII",
    0x45,
    0,
    ip_total_len,
    0xB1A5,
    0,
    64,
    socket.IPPROTO_UDP,
    0,
    source,
    destination,
)
ip = struct.pack(
    "!BBHHHBBHII",
    0x45,
    0,
    ip_total_len,
    0xB1A5,
    0,
    64,
    socket.IPPROTO_UDP,
    checksum(ip_without_checksum),
    source,
    destination,
)
frame = b"\xaa" * 6 + b"\xbb" * 6 + struct.pack("!H", 0x0800) + ip + udp + payload

sink = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
src = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
try:
    sink.bind((sink_if, 0))
    sink.setblocking(False)
    while select.select([sink], [], [], 0)[0]:
        sink.recv(65535)

    src.bind((src_if, 0))
    src.send(frame)
    deadline = time.monotonic() + 2
    received = None
    while time.monotonic() < deadline:
        ready, _, _ = select.select([sink], [], [], deadline - time.monotonic())
        if not ready:
            break
        packet = sink.recv(65535)
        if (
            len(packet) >= len(frame)
            and packet[12:22] == frame[12:22]
            and packet[23:34] == frame[23:34]
            and packet[34 : len(frame)] == frame[34 : len(frame)]
        ):
            received = packet[: len(frame)]
            break
finally:
    sink.close()
    src.close()

if expected == "deliver":
    if received is None:
        raise SystemExit(f"port {src_port} was expected to deliver but dropped")
    print(f"port {src_port} delivered successfully")
elif expected == "drop":
    if received is not None:
        raise SystemExit(f"port {src_port} was expected to drop but delivered")
    print(f"port {src_port} dropped successfully")
PY
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

SERVICE_DEST=10.0.0.99/32 "${LOADER}" "${IN_IF}" "${OUT_IF}" >"${LOG}" 2>&1 &
LOADER_PID=$!

sleep 1
if ! kill -0 "${LOADER_PID}" 2>/dev/null; then
	cat "${LOG}" >&2 || true
	printf '%s\n' "loader exited before blocked-port smoke" >&2
	exit 1
fi

"${DPSTAT}" set-nexthop 1 aa:aa:aa:aa:aa:aa bb:bb:bb:bb:bb:bb

# 1. Set blocked port 9999
"${DPSTAT}" set-blocked-ports 9999

# Verify src-port 9999 drops, src-port 9998 delivers
probe 9999 drop
probe 9998 deliver

# Verify drop counter updated in snapshot
snapshot=$("${DPSTAT}" snapshot --json)
if ! python3 - "${snapshot}" <<'PY'
import json
import sys

snapshot = json.loads(sys.argv[1])
drop_count = snapshot["node"]["counters"]["udp_amplification_drop"]
if drop_count < 1:
    raise SystemExit(f"udp_amplification_drop counter is {drop_count}, expected >= 1")
print(f"udp_amplification_drop counter verified: {drop_count}")
PY
then
	printf '%s\n' "${snapshot}" >&2
	exit 1
fi

# 2. Perform a service apply (slot flip) using xdpgw-apply and golden fixture
python3 tests/fixtures/gen_apply_snapshot_golden.py
"${APPLY}" tests/fixtures/apply_snapshot_golden.bin
"${DPSTAT}" set-nexthop 42 aa:aa:aa:aa:aa:aa bb:bb:bb:bb:bb:bb

# Verify src-port 9999 still drops after slot flip (carry-forward safe across both slots)
probe 9999 drop

# 3. Clear blocked ports
"${DPSTAT}" set-blocked-ports

# Verify src-port 9999 now delivers cleanly
probe 9999 deliver

printf '%s\n' "blocked-port smoke passed successfully"
