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
IN_IF=nhin0
SRC_IF=nhsrc0
OUT_IF=nhout0
SINK_IF=nhsink0
LOG=${TMPDIR:-/tmp}/xdp-gateway-nexthop-smoke.$$.log
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
	rm -f "${PASS_PIN}" "${PASS_SRC}" "${PASS_OBJ}" "${LOG}"
}

trap cleanup EXIT
cleanup

ip link add "${SRC_IF}" type veth peer name "${IN_IF}"
ip link add "${SINK_IF}" type veth peer name "${OUT_IF}"

for iface in "${SRC_IF}" "${IN_IF}" "${SINK_IF}" "${OUT_IF}"; do
	ip link set dev "${iface}" up
done

# Assign IP to SINK_IF so Linux kernel will reply to ARP requests on SINK_IF
ip addr add 10.0.0.99/24 dev "${SINK_IF}"

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

SERVICE_DEST=10.0.0.2 "${LOADER}" "${IN_IF}" "${OUT_IF}" >"${LOG}" 2>&1 &
LOADER_PID=$!

sleep 1
if ! kill -0 "${LOADER_PID}" 2>/dev/null; then
	cat "${LOG}" >&2 || true
	echo "loader exited before smoke could send a frame" >&2
	exit 1
fi

SINK_MAC=$(cat /sys/class/net/"${SINK_IF}"/address)
OUT_MAC=$(cat /sys/class/net/"${OUT_IF}"/address)

# Helper python script to send an IPv4 frame and test delivery & MAC rewrite
send_probe() {
	local expect_action=$1
	local expected_dst_mac=${2:-""}
	local expected_src_mac=${3:-""}

	python3 - "${SRC_IF}" "${SINK_IF}" "${expect_action}" "${expected_dst_mac}" "${expected_src_mac}" <<'PY'
import select
import socket
import struct
import sys
import time

src_if, sink_if, expect_action, expected_dst_mac_str, expected_src_mac_str = sys.argv[1:]

def checksum(data):
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    total = (total & 0xffff) + (total >> 16)
    total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff

def mac_to_bytes(mac_str):
    return bytes.fromhex(mac_str.replace(":", ""))

src_ip = struct.unpack("!I", socket.inet_aton("45.45.0.1"))[0]
dst_ip = struct.unpack("!I", socket.inet_aton("10.0.0.2"))[0]
payload = b"nexthop-smoke-payload"
udp = struct.pack("!HHHH", 1234, 53, 8 + len(payload), 0)
ip_total_len = 20 + len(udp) + len(payload)
ip_no_csum = struct.pack(
    "!BBHHHBBHII",
    0x45, 0, ip_total_len, 0x1234, 0, 64, socket.IPPROTO_UDP, 0, src_ip, dst_ip
)
ip = struct.pack(
    "!BBHHHBBHII",
    0x45, 0, ip_total_len, 0x1234, 0, 64, socket.IPPROTO_UDP, checksum(ip_no_csum), src_ip, dst_ip
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
    if len(pkt) >= len(frame) and pkt[12:34] == frame[12:34]:
        received = pkt
        break

if expect_action == "deliver":
    if received is None:
        raise SystemExit("expected delivered frame, but non received")
    
    ttl_off = 14 + 8
    csum_off = 14 + 10
    if received[ttl_off] != frame[ttl_off]:
        raise SystemExit("TTL changed: got %d want %d" % (received[ttl_off], frame[ttl_off]))
    if received[csum_off : csum_off + 2] != frame[csum_off : csum_off + 2]:
        raise SystemExit("IPv4 checksum changed")
    
    if expected_dst_mac_str:
        exp_dst = mac_to_bytes(expected_dst_mac_str)
        if received[:6] != exp_dst:
            raise SystemExit("dst MAC mismatch: got %s want %s" % (received[:6].hex(), exp_dst.hex()))
    
    if expected_src_mac_str:
        exp_src = mac_to_bytes(expected_src_mac_str)
        if received[6:12] != exp_src:
            raise SystemExit("src MAC mismatch: got %s want %s" % (received[6:12].hex(), exp_src.hex()))
            
    print("probe delivered: MAC rewritten, TTL/csum unchanged")
elif expect_action == "drop":
    if received is not None:
        raise SystemExit("expected drop, but frame was delivered")
    print("probe dropped as expected")
PY
}

# Step 1: Unresolved fail-closed drop
echo "Testing unresolved fail-closed drop..."
snap1=$("${DPSTAT}" snapshot --json)
unres1=$(python3 -c "import json, sys; print(json.loads('''$snap1''')['node']['counters']['nexthop_unresolved'])")
send_probe drop
snap2=$("${DPSTAT}" snapshot --json)
unres2=$(python3 -c "import json, sys; print(json.loads('''$snap2''')['node']['counters']['nexthop_unresolved'])")
if [ "$((unres2 - unres1))" -ne 1 ]; then
	echo "Expected nexthop_unresolved counter incremented by 1, got $((unres2 - unres1))" >&2
	exit 1
fi

# Step 2: Static set-nexthop rewrite
echo "Testing static set-nexthop..."
CUSTOM_DST="02:00:00:00:00:99"
"${DPSTAT}" set-nexthop 1 "${CUSTOM_DST}" "${OUT_MAC}"
send_probe deliver "${CUSTOM_DST}" "${OUT_MAC}"

# Step 3: Real ARP resolve-nexthop against peer
echo "Testing resolve-nexthop via ARP..."
"${DPSTAT}" resolve-nexthop 1 10.0.0.99
send_probe deliver "${SINK_MAC}" "${OUT_MAC}"

snap3=$("${DPSTAT}" snapshot --json)
python3 - "${snap3}" "${SINK_MAC}" <<'PY'
import json, sys
snap = json.loads(sys.argv[1])
sink_mac = sys.argv[2].lower().replace(":", "")
nh_list = snap.get("nexthop", [])
found = False
for nh in nh_list:
    if nh.get("dp_id") == 1:
        found = True
        if nh.get("resolved") != 1:
            raise SystemExit("snapshot nexthop entry resolved != 1")
        if nh.get("dst_mac").lower().replace(":", "") != sink_mac:
            raise SystemExit("snapshot dst_mac mismatch: %s vs %s" % (nh.get("dst_mac"), sink_mac))
if not found:
    raise SystemExit("dp_id 1 not found in snapshot nexthop list")
print("snapshot nexthop verification passed")
PY

# Step 4: Evict nexthop
echo "Testing evict-nexthop..."
"${DPSTAT}" evict-nexthop 1
send_probe drop

echo "smoke_nexthop passed successfully!"
