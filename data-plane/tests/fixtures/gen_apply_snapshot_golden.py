#!/usr/bin/env python3
"""Generate apply_snapshot_golden.bin — the committed CP<->DP wire fixture.

This encodes the apply_snapshot.h v3 layout by hand (explicit little-endian,
network-order IPv4). The C parser (tools/xdpgw-apply.c parse_snapshot) decodes
it in tests/test_snapshot.c, and the Python serializer (M4 #2 T6
serialize_node_snapshot) must emit byte-identical output for the matching node.
Regenerate with: python3 gen_apply_snapshot_golden.py

Keep this in lock-step with data-plane/src/apply_snapshot.h. Bump the schema
version there and here together on any layout change.
"""
import socket
import struct

MAGIC = b"XDPGWAP1"
SCHEMA_VERSION = 3

# Flag bits (mirror the data-plane headers).
WL_F_ACTIVE = 1 << 0
VIP_F_PPS_SET = 1 << 0
VIP_F_BPS_SET = 1 << 1
SVC_RL_F_PPS_SET = 1 << 0
SVC_RL_F_BPS_SET = 1 << 1
RULE_F_ENABLED = 1 << 0

APPLY_SNAPSHOT_KIND_SERVICE_FULL = 1


def be32(ipv4: str) -> bytes:
    """IPv4 dotted-quad -> 4 network-order bytes (matches DP __be32 keys)."""
    return socket.inet_aton(ipv4)


def service(*, dst_prefixlen, dst_ip, dp_id, enabled, wl_flags, bl_flags,
            committed_bps, ceiling_bps, vip_pps, vip_bps, vip_flags,
            service_pps, service_bps, svc_rl_flags,
            rules, whitelist, sbl) -> bytes:
    b = struct.pack("<I", dst_prefixlen)
    b += be32(dst_ip)
    b += struct.pack("<I", dp_id)
    b += struct.pack("<BBB", enabled, wl_flags, bl_flags)
    b += struct.pack("<QQ", committed_bps, ceiling_bps)
    b += struct.pack("<QQ", vip_pps, vip_bps)
    b += struct.pack("<B", vip_flags)
    b += struct.pack("<QQ", service_pps, service_bps)
    b += struct.pack("<B", svc_rl_flags)
    b += struct.pack("<H", len(rules))
    for src_lo, src_hi, dst_lo, dst_hi, proto, flags in rules:
        b += struct.pack("<HHHHBB", src_lo, src_hi, dst_lo, dst_hi, proto, flags)
    b += struct.pack("<I", len(whitelist))
    for prefixlen, ip in whitelist:
        b += struct.pack("<I", prefixlen) + be32(ip)
    b += struct.pack("<I", len(sbl))
    for prefixlen, ip in sbl:
        b += struct.pack("<I", prefixlen) + be32(ip)
    return b


def build() -> bytes:
    services = [
        # Rich service: enabled, VIP active (both dims), one match-all rule,
        # one whitelist source CIDR, one service-blacklist source.
        service(
            dst_prefixlen=32, dst_ip="10.0.0.2", dp_id=42, enabled=1,
            wl_flags=WL_F_ACTIVE, bl_flags=0,
            committed_bps=1_000_000_000, ceiling_bps=2_000_000_000,
            vip_pps=1000, vip_bps=8_000_000,
            vip_flags=VIP_F_PPS_SET | VIP_F_BPS_SET,
            service_pps=0, service_bps=0, svc_rl_flags=0,
            rules=[(0, 65535, 0, 65535, 0, RULE_F_ENABLED)],
            whitelist=[(24, "192.51.100.0")],
            sbl=[(32, "203.0.113.5")],
        ),
        # Minimal service: enabled, no VIP, no rules, empty lists.
        service(
            dst_prefixlen=32, dst_ip="10.0.0.3", dp_id=43, enabled=1,
            wl_flags=0, bl_flags=0,
            committed_bps=0, ceiling_bps=500_000_000,
            vip_pps=0, vip_bps=0, vip_flags=0,
            service_pps=0, service_bps=0, svc_rl_flags=0,
            rules=[], whitelist=[], sbl=[],
        ),
    ]
    out = (
        MAGIC
        + struct.pack("<I", SCHEMA_VERSION)
        + struct.pack("<I", APPLY_SNAPSHOT_KIND_SERVICE_FULL)
        + struct.pack("<I", len(services))
    )
    for s in services:
        out += s
    return out


if __name__ == "__main__":
    import os

    data = build()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "apply_snapshot_golden.bin")
    with open(path, "wb") as fh:
        fh.write(data)
    print(f"wrote {path} ({len(data)} bytes)")
