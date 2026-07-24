#!/usr/bin/env bash
set -eu

# DT4 — live veth smoke for the per-service clean-path rate limit (svc_rl).
#
# Two loader runs over the same veth pair prove the four SVR acceptance points:
#   Run 1 (enforcing, XDPGW_SEED_SVC_PPS set):
#     * a non-whitelisted source is rate-limited: only the ~1-packet burst is
#       redirected (clean rises), the rest become rate_limit_drop (over budget).
#     * a whitelisted source on the same service is unaffected — it takes the
#       VIP path (high ceiling) and every frame is redirected (SVR-05).
#   Run 2 (unlimited, no svc seed):
#     * both dimensions unset => zero rate_limit_drop, every frame redirected
#       (SVR-04, "0 = block by accident" must never happen).
#
# The fairness ladder is pinned wide open in both runs so svc_rl is the only
# stage that can drop these few frames.

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
IN_IF=rlin0
SRC_IF=rlsrc0
OUT_IF=rlout0
SINK_IF=rlsink0
LOG=${TMPDIR:-/tmp}/xdp-gateway-ratelimit.$$.log
PASS_SRC=${TMPDIR:-/tmp}/xdp-gateway-ratelimit-pass.$$.bpf.c
PASS_OBJ=${TMPDIR:-/tmp}/xdp-gateway-ratelimit-pass.$$.bpf.o
PASS_PIN=/sys/fs/bpf/xdp_gateway_ratelimit_pass_$$
LOADER_PID=

DST_IP=10.0.0.2
SRC_WL=45.45.0.1   # whitelisted source (VIP path, must be unaffected)
SRC_CLEAN=45.45.0.2 # non-whitelisted source (rule -> svc_rl path)
NUM=16
POSSIBLE_CPUS=$(awk -F, '{ for (i = 1; i <= NF; i++) { split($i, r, "-"); n += r[2] ? r[2] - r[1] + 1 : 1 } } END { print n }' /sys/devices/system/cpu/possible)
# rl_burst() = max(pps / ncpus, 1); pps == ncpus gives a deterministic 1-packet
# per-CPU burst, so all but ~1 clean frame is dropped over budget.
SVC_PPS=${POSSIBLE_CPUS}
VIP_PPS=1000000
WIDE_BPS=100000000000 # 100 Gbps: fairness never drops these frames

kill_loader()
{
	if [ -n "${LOADER_PID}" ] && kill -0 "${LOADER_PID}" 2>/dev/null; then
		kill "${LOADER_PID}" 2>/dev/null || true
		wait "${LOADER_PID}" 2>/dev/null || true
	fi
	LOADER_PID=
}

cleanup()
{
	kill_loader
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

# Drive NUM frames from each of two source IPs into IN and count how many of each
# arrive redirected at SINK. Prints: "A=<wl-redirected> B=<clean-redirected>".
drive()
{
	python3 - "${SRC_IF}" "${SINK_IF}" "${DST_IP}" "${SRC_WL}" "${SRC_CLEAN}" "${NUM}" <<'PY'
import select
import socket
import struct
import sys
import time

src_if, sink_if, dst_ip, src_wl, src_clean, num = sys.argv[1:7]
num = int(num)


def checksum(data):
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def frame(src_ip):
    src = struct.unpack("!I", socket.inet_aton(src_ip))[0]
    dst = struct.unpack("!I", socket.inet_aton(dst_ip))[0]
    payload = b"x" * 18
    udp = struct.pack("!HHHH", 1234, 53, 8 + len(payload), 0)
    total_len = 20 + len(udp) + len(payload)
    hdr = lambda csum: struct.pack(
        "!BBHHHBBHII", 0x45, 0, total_len, 0x1234, 0, 64,
        socket.IPPROTO_UDP, csum, src, dst)
    ip = hdr(checksum(hdr(0)))
    return (b"\xaa" * 6) + (b"\xbb" * 6) + struct.pack("!H", 0x0800) + ip + udp + payload


f_wl = frame(src_wl)
f_clean = frame(src_clean)
wl_src = socket.inet_aton(src_wl)
clean_src = socket.inet_aton(src_clean)

sink = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
sink.bind((sink_if, 0))
sink.setblocking(False)
while True:
    ready, _, _ = select.select([sink], [], [], 0)
    if not ready:
        break
    sink.recv(65535)

tx = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
tx.bind((src_if, 0))
for _ in range(num):
    tx.send(f_wl)
for _ in range(num):
    tx.send(f_clean)

a = b = 0
deadline = time.time() + 3
while time.time() < deadline:
    ready, _, _ = select.select([sink], [], [], max(0, deadline - time.time()))
    if not ready:
        break
    pkt = sink.recv(65535)
    if len(pkt) < 30 or pkt[12:14] != b"\x08\x00":
        continue
    ip_src = pkt[26:30]
    if ip_src == wl_src:
        a += 1
    elif ip_src == clean_src:
        b += 1

print("A=%d B=%d" % (a, b))
PY
}

rate_limit_drops()
{
	"${DPSTAT}" counters | awk '$2 == "rate_limit_drop" { print $3; found = 1 } END { if (!found) print 0 }'
}

# ---- Run 1: enforcing (svc_rl seeded) ----------------------------------------
SERVICE_DEST="${DST_IP}" \
XDPGW_SEED_SVC_PPS="${SVC_PPS}" \
XDPGW_SEED_WL_CIDR="${SRC_WL}/32" \
XDPGW_SEED_VIP_PPS="${VIP_PPS}" \
XDPGW_FAIR_COMMITTED_BPS="${WIDE_BPS}" \
XDPGW_FAIR_CEILING_BPS="${WIDE_BPS}" \
XDPGW_NODE_CLEAN_CAPACITY_BPS="${WIDE_BPS}" \
	"${LOADER}" "${IN_IF}" "${OUT_IF}" >"${LOG}" 2>&1 &
LOADER_PID=$!
sleep 1
if ! kill -0 "${LOADER_PID}" 2>/dev/null; then
	sed -n '1,240p' "${LOG}" >&2 || true
	echo "loader exited before rate-limit smoke run 1" >&2
	exit 1
fi
"${DPSTAT}" set-nexthop 1 aa:aa:aa:aa:aa:aa bb:bb:bb:bb:bb:bb

counts=$(drive)
wl_redir=$(printf '%s\n' "${counts}" | sed -n 's/.*A=\([0-9]*\).*/\1/p')
clean_redir=$(printf '%s\n' "${counts}" | sed -n 's/.*B=\([0-9]*\).*/\1/p')
rl_drops=$(rate_limit_drops)
echo "run1 enforcing: whitelisted_redirected=${wl_redir} clean_redirected=${clean_redir} rate_limit_drop=${rl_drops}"

if [ "${wl_redir}" -ne "${NUM}" ]; then
	sed -n '1,240p' "${LOG}" >&2 || true
	echo "expected all ${NUM} whitelisted frames redirected (VIP path unaffected), got ${wl_redir}" >&2
	exit 1
fi
if [ "${clean_redir}" -lt 1 ] || [ "${clean_redir}" -ge "${NUM}" ]; then
	sed -n '1,240p' "${LOG}" >&2 || true
	echo "expected clean burst 1..$((NUM - 1)) frames redirected, got ${clean_redir}" >&2
	exit 1
fi
if [ "${rl_drops}" -le 0 ]; then
	"${DPSTAT}" counters >&2 || true
	echo "expected live rate_limit_drop counter to be positive" >&2
	exit 1
fi
kill_loader

# ---- Run 2: unlimited (no svc seed) ------------------------------------------
SERVICE_DEST="${DST_IP}" \
XDPGW_FAIR_COMMITTED_BPS="${WIDE_BPS}" \
XDPGW_FAIR_CEILING_BPS="${WIDE_BPS}" \
XDPGW_NODE_CLEAN_CAPACITY_BPS="${WIDE_BPS}" \
	"${LOADER}" "${IN_IF}" "${OUT_IF}" >"${LOG}" 2>&1 &
LOADER_PID=$!
sleep 1
if ! kill -0 "${LOADER_PID}" 2>/dev/null; then
	sed -n '1,240p' "${LOG}" >&2 || true
	echo "loader exited before rate-limit smoke run 2" >&2
	exit 1
fi
"${DPSTAT}" set-nexthop 1 aa:aa:aa:aa:aa:aa bb:bb:bb:bb:bb:bb

counts=$(drive)
clean_redir=$(printf '%s\n' "${counts}" | sed -n 's/.*B=\([0-9]*\).*/\1/p')
rl_drops=$(rate_limit_drops)
echo "run2 unlimited: clean_redirected=${clean_redir} rate_limit_drop=${rl_drops}"

if [ "${clean_redir}" -ne "${NUM}" ]; then
	sed -n '1,240p' "${LOG}" >&2 || true
	echo "expected all ${NUM} clean frames redirected when rate limit unset, got ${clean_redir}" >&2
	exit 1
fi
if [ "${rl_drops}" -ne 0 ]; then
	"${DPSTAT}" counters >&2 || true
	echo "expected zero rate_limit_drop when service rate limit unset, got ${rl_drops}" >&2
	exit 1
fi
kill_loader

echo "svc rate-limit smoke: over-budget drops, whitelist unaffected, unset => no drops"
