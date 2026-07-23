#!/usr/bin/env python3
"""Generate v1 apply snapshots and probe real-veth apply verdicts."""

import argparse
import select
import socket
import struct
import sys
import time
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(FIXTURES))

from gen_apply_snapshot_golden import MAGIC, RULE_F_ENABLED, service

APPLY_SNAPSHOT_SCHEMA_VERSION = 4
APPLY_SNAPSHOT_KIND_SERVICE_FULL = 1
APPLY_SNAPSHOT_KIND_GLOBAL_DENY = 2
GLOBAL_DENY_MAX_ENTRIES = 1_048_576


MATCH_ALL_RULE = [(0, 65535, 0, 65535, 0, RULE_F_ENABLED)]


def snapshot_service(dst_ip: str, dp_id: int) -> bytes:
    return service(
        dst_prefixlen=32,
        dst_ip=dst_ip,
        dp_id=dp_id,
        enabled=1,
        wl_flags=0,
        reserved0=0,
        committed_bps=0,
        ceiling_bps=500_000_000,
        vip_pps=0,
        vip_bps=0,
        vip_flags=0,
        service_pps=0,
        service_bps=0,
        svc_rl_flags=0,
        rules=MATCH_ALL_RULE,
        whitelist=[],
    )


def write_snapshot(path: Path, services: list[bytes]) -> None:
    path.write_bytes(
        MAGIC
        + struct.pack(
            "<III",
            APPLY_SNAPSHOT_SCHEMA_VERSION,
            APPLY_SNAPSHOT_KIND_SERVICE_FULL,
            len(services),
        )
        + b"".join(services)
    )


def generate_small(path: Path) -> None:
    write_snapshot(path, [snapshot_service("10.0.0.3", 2)])


def generate_bulk(path: Path, count: int) -> None:
    if count <= 0:
        raise ValueError("bulk service count must be positive")

    services = []
    for index in range(count):
        third = index // 254
        fourth = index % 254 + 1
        services.append(snapshot_service(f"10.128.{third}.{fourth}", index + 1))
    write_snapshot(path, services)


def write_global_snapshot(path: Path, *, count: int, include_entries: bool) -> None:
    if count < 0:
        raise ValueError("global entry count must not be negative")
    if include_entries and count > GLOBAL_DENY_MAX_ENTRIES:
        raise ValueError("global entry count exceeds the helper limit")

    with path.open("wb") as snapshot:
        snapshot.write(
            MAGIC
            + struct.pack(
                "<IIQI",
                APPLY_SNAPSHOT_SCHEMA_VERSION,
                APPLY_SNAPSHOT_KIND_GLOBAL_DENY,
                42,
                count,
            )
        )
        if include_entries:
            for address in range(0x2D000000, 0x2D000000 + count):
                snapshot.write(struct.pack("<I", 32))
                snapshot.write(struct.pack("!I", address))


def generate_global_small(path: Path) -> None:
    write_global_snapshot(path, count=1, include_entries=True)


def generate_global_scale(path: Path, count: int) -> None:
    write_global_snapshot(path, count=count, include_entries=True)


def generate_global_too_many(path: Path) -> None:
    write_global_snapshot(
        path, count=GLOBAL_DENY_MAX_ENTRIES + 1, include_entries=False
    )


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def frame(source_ip: str, destination_ip: str) -> bytes:
    source = struct.unpack("!I", socket.inet_aton(source_ip))[0]
    destination = struct.unpack("!I", socket.inet_aton(destination_ip))[0]
    payload = b"apply-smoke"
    udp = struct.pack("!HHHH", 1234, 53, 8 + len(payload), 0)
    total_length = 20 + len(udp) + len(payload)
    ip_without_checksum = struct.pack(
        "!BBHHHBBHII",
        0x45,
        0,
        total_length,
        0xA551,
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
        total_length,
        0xA551,
        0,
        64,
        socket.IPPROTO_UDP,
        checksum(ip_without_checksum),
        source,
        destination,
    )
    return b"\xaa" * 6 + b"\xbb" * 6 + struct.pack("!H", 0x0800) + ip + udp + payload


def expect_verdict(
    source_if: str,
    sink_if: str,
    source_ip: str,
    destination_ip: str,
    want_delivery: bool,
) -> None:
    expected = frame(source_ip, destination_ip)
    sink = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
    source = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    try:
        sink.bind((sink_if, 0))
        sink.setblocking(False)
        while select.select([sink], [], [], 0)[0]:
            sink.recv(65535)

        source.bind((source_if, 0))
        source.send(expected)
        delivered = False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            ready, _, _ = select.select(
                [sink], [], [], deadline - time.monotonic()
            )
            if not ready:
                break
            if sink.recv(65535)[: len(expected)] == expected:
                delivered = True
                break
    finally:
        sink.close()
        source.close()

    if delivered != want_delivery:
        verdict = "delivery" if want_delivery else "drop"
        raise SystemExit(f"{source_ip} -> {destination_ip}: expected {verdict}")
    state = "delivered" if delivered else "dropped"
    print(f"{source_ip} -> {destination_ip}: {state}")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    small = commands.add_parser("generate-small")
    small.add_argument("path", type=Path)
    bulk = commands.add_parser("generate-bulk")
    bulk.add_argument("path", type=Path)
    bulk.add_argument("count", type=int)
    global_small = commands.add_parser("generate-global-small")
    global_small.add_argument("path", type=Path)
    global_scale = commands.add_parser("generate-global-scale")
    global_scale.add_argument("path", type=Path)
    global_scale.add_argument("count", type=int)
    global_too_many = commands.add_parser("generate-global-too-many")
    global_too_many.add_argument("path", type=Path)
    expect = commands.add_parser("expect")
    expect.add_argument("source_if")
    expect.add_argument("sink_if")
    expect.add_argument("source_ip")
    expect.add_argument("destination_ip")
    expect.add_argument("verdict", choices=("deliver", "drop"))
    args = parser.parse_args()

    if args.command == "generate-small":
        generate_small(args.path)
    elif args.command == "generate-bulk":
        generate_bulk(args.path, args.count)
    elif args.command == "generate-global-small":
        generate_global_small(args.path)
    elif args.command == "generate-global-scale":
        generate_global_scale(args.path, args.count)
    elif args.command == "generate-global-too-many":
        generate_global_too_many(args.path)
    else:
        expect_verdict(
            args.source_if,
            args.sink_if,
            args.source_ip,
            args.destination_ip,
            args.verdict == "deliver",
        )


if __name__ == "__main__":
    main()
