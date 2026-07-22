import asyncio
import ipaddress
import json
import logging
import os
import struct
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.models import (
    AllowRule,
    BlacklistEntry,
    ProtectedService,
    ServiceMode,
    ServicePlan,
    WhitelistEntry,
)
from app.services.feed_reconcile import MAX_GLOBAL_DENY_ENTRIES, GlobalDenySnapshot

logger = logging.getLogger(__name__)

APPLY_SNAPSHOT_MAGIC = b"XDPGWAP1"
APPLY_SNAPSHOT_SCHEMA_VERSION = 3
APPLY_SNAPSHOT_KIND_SERVICE_FULL = 1
APPLY_SNAPSHOT_KIND_GLOBAL_DENY = 2
_GBPS = 1_000_000_000
_WL_F_ACTIVE = 1 << 0
_WL_F_HAS_BROAD = 1 << 1
_VIP_F_PPS_SET = 1 << 0
_VIP_F_BPS_SET = 1 << 1
_SVC_RL_F_PPS_SET = 1 << 0
_SVC_RL_F_BPS_SET = 1 << 1
_RULE_F_ENABLED = 1 << 0
_RULE_F_PPS_SET = 1 << 1
_RULE_F_BPS_SET = 1 << 2
_BLOOM_PREFIX = 24
_PROTOCOL_NUMBERS = {
    "any": 0,
    "icmp": 1,
    "tcp": 6,
    "udp": 17,
}


@dataclass(frozen=True)
class ServiceConfig:
    service_id: uuid.UUID
    dp_id: int
    version: int
    name: str
    cidr_or_ip: str
    mode: ServiceMode
    enabled: bool
    vip_pps: int | None
    vip_bps: int | None
    service_pps: int | None
    service_bps: int | None
    plan: ServicePlan | None
    rules: tuple[AllowRule, ...]
    whitelist: tuple[WhitelistEntry, ...]
    blacklist: tuple[BlacklistEntry, ...]


class Applier(Protocol):
    async def apply(self, config: ServiceConfig) -> None: ...


class PlaceholderApplier:
    async def apply(self, config: ServiceConfig) -> None:
        logger.info(
            "placeholder apply acknowledged",
            extra={
                "service_id": str(config.service_id),
                "service_name": config.name,
                "version": config.version,
                "rule_count": len(config.rules),
                "whitelist_count": len(config.whitelist),
                "blacklist_count": len(config.blacklist),
            },
        )


class ApplyError(RuntimeError):
    """The external apply helper did not complete a swap."""


@dataclass(frozen=True, slots=True)
class GlobalDenyApplyResult:
    active_slot: int
    node_map_version: int


class DoubleBufferApplier:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        apply_bin: str,
        timeout_seconds: float,
    ) -> None:
        self._session_factory = session_factory
        self._apply_bin = apply_bin
        self._timeout = timeout_seconds

    async def apply(self, config: ServiceConfig) -> None:
        started = time.monotonic()
        async with self._session_factory() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            node = await load_node_config(db)

        snapshot_fd, snapshot_path = tempfile.mkstemp(prefix="xdpgw-apply-", suffix=".bin")
        try:
            os.fchmod(snapshot_fd, 0o600)
            with os.fdopen(snapshot_fd, "wb") as snapshot:
                snapshot.write(serialize_node_snapshot(node))

            try:
                process = await asyncio.create_subprocess_exec(
                    self._apply_bin,
                    snapshot_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                raise ApplyError(f"unable to start xdpgw-apply: {exc}") from exc

            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
            except TimeoutError as exc:
                process.kill()
                _, stderr = await process.communicate()
                detail = _stderr_text(stderr)
                message = "xdpgw-apply timed out"
                if detail:
                    message = f"{message}: {detail}"
                raise ApplyError(message) from exc

            if process.returncode != 0:
                detail = _stderr_text(stderr)
                if not detail:
                    detail = f"exit status {process.returncode}"
                raise ApplyError(detail)
        finally:
            os.unlink(snapshot_path)

        logger.info(
            "double-buffer apply completed",
            extra={
                "service_id": str(config.service_id),
                "version": config.version,
                "service_count": len(node),
                "verify_result": "passed",
                "duration_ms": int((time.monotonic() - started) * 1_000),
            },
        )


class GlobalDenyApplier:
    """Run the v2 GLOBAL_DENY helper boundary without holding a DB transaction."""

    def __init__(self, *, apply_bin: str, timeout_seconds: float) -> None:
        self._apply_bin = apply_bin
        self._timeout = timeout_seconds

    async def apply_global(self, snapshot: GlobalDenySnapshot) -> GlobalDenyApplyResult:
        snapshot_fd, snapshot_path = tempfile.mkstemp(prefix="xdpgw-global-deny-", suffix=".bin")
        try:
            os.fchmod(snapshot_fd, 0o600)
            with os.fdopen(snapshot_fd, "wb") as snapshot_file:
                snapshot_file.write(serialize_global_snapshot(snapshot))

            try:
                process = await asyncio.create_subprocess_exec(
                    self._apply_bin,
                    snapshot_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                raise ApplyError(f"unable to start xdpgw-apply: {exc}") from exc

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self._timeout
                )
            except TimeoutError as exc:
                process.kill()
                _, stderr = await process.communicate()
                detail = _stderr_text(stderr)
                message = "xdpgw-apply timed out"
                if detail:
                    message = f"{message}: {detail}"
                raise ApplyError(message) from exc

            if process.returncode != 0:
                detail = _stderr_text(stderr)
                if not detail:
                    detail = f"exit status {process.returncode}"
                raise ApplyError(detail)
            return _parse_global_apply_result(stdout)
        finally:
            os.unlink(snapshot_path)


async def load_service_config(
    db: AsyncSession,
    service_id: uuid.UUID,
) -> ServiceConfig | None:
    service = (
        (
            await db.execute(
                select(ProtectedService)
                .options(
                    selectinload(ProtectedService.plan),
                    selectinload(ProtectedService.rules),
                    selectinload(ProtectedService.whitelist_entries),
                    selectinload(ProtectedService.blacklist_entries),
                )
                .where(ProtectedService.id == service_id)
            )
        )
        .scalars()
        .one_or_none()
    )
    if service is None:
        return None

    return _service_config(service)


async def load_node_config(db: AsyncSession) -> tuple[ServiceConfig, ...]:
    """Load one consistent, enabled-service snapshot for a node apply."""
    services = list(
        (
            await db.execute(
                select(ProtectedService)
                .options(
                    selectinload(ProtectedService.plan),
                    selectinload(ProtectedService.rules),
                    selectinload(ProtectedService.whitelist_entries),
                    selectinload(ProtectedService.blacklist_entries),
                )
                .where(ProtectedService.enabled.is_(True))
                .order_by(ProtectedService.dp_id)
            )
        )
        .scalars()
        .all()
    )
    return tuple(_service_config(service) for service in services)


def serialize_node_snapshot(node: tuple[ServiceConfig, ...]) -> bytes:
    """Encode the explicit v3 SERVICE_FULL apply_snapshot.h wire format."""
    payload = bytearray()
    payload.extend(APPLY_SNAPSHOT_MAGIC)
    payload.extend(
        struct.pack(
            "<III",
            APPLY_SNAPSHOT_SCHEMA_VERSION,
            APPLY_SNAPSHOT_KIND_SERVICE_FULL,
            len(node),
        )
    )
    for service in node:
        dst_prefixlen, dst_addr = _cidr_parts(service.cidr_or_ip)
        whitelist = tuple(_cidr_parts(entry.source_cidr) for entry in service.whitelist)
        blacklist = tuple(_cidr_parts(entry.source_cidr) for entry in service.blacklist)
        vip_flags = _vip_flags(service)
        svc_rl_flags = _svc_rl_flags(service)
        wl_flags = _list_flags(whitelist, active=bool(whitelist))
        bl_flags = 0
        committed_bps, ceiling_bps = _plan_rates(service.plan)

        payload.extend(
            struct.pack(
                "<I4sIBBBQQQQBQQBH",
                dst_prefixlen,
                dst_addr,
                service.dp_id,
                int(service.enabled),
                wl_flags,
                bl_flags,
                committed_bps,
                ceiling_bps,
                service.vip_pps or 0,
                service.vip_bps or 0,
                vip_flags,
                service.service_pps or 0,
                service.service_bps or 0,
                svc_rl_flags,
                len(service.rules),
            )
        )
        for rule in service.rules:
            payload.extend(
                struct.pack(
                    "<HHHHBB",
                    rule.src_port_lo or 0,
                    rule.src_port_hi if rule.src_port_hi is not None else 65_535,
                    rule.dst_port_lo or 0,
                    rule.dst_port_hi if rule.dst_port_hi is not None else 65_535,
                    _PROTOCOL_NUMBERS[rule.protocol.value],
                    _rule_flags(rule),
                )
            )
        _append_source_list(payload, whitelist)
        _append_source_list(payload, blacklist)
    return bytes(payload)


def serialize_global_snapshot(snapshot: GlobalDenySnapshot) -> bytes:
    """Encode sorted canonical global CIDRs for the v2 GLOBAL_DENY helper mode."""
    if len(snapshot.cidrs) > MAX_GLOBAL_DENY_ENTRIES:
        raise ValueError(f"Global deny entry limit is {MAX_GLOBAL_DENY_ENTRIES}")

    entries = tuple(sorted(_strict_ipv4_cidr_parts(cidr) for cidr in snapshot.cidrs))
    payload = bytearray(APPLY_SNAPSHOT_MAGIC)
    payload.extend(
        struct.pack(
            "<IIQI",
            APPLY_SNAPSHOT_SCHEMA_VERSION,
            APPLY_SNAPSHOT_KIND_GLOBAL_DENY,
            snapshot.revision,
            len(entries),
        )
    )
    for prefixlen, address in entries:
        payload.extend(struct.pack("<I4s", prefixlen, address))
    return bytes(payload)


def _service_config(service: ProtectedService) -> ServiceConfig:
    return ServiceConfig(
        service_id=service.id,
        dp_id=service.dp_id,
        version=service.version,
        name=service.name,
        cidr_or_ip=service.cidr_or_ip,
        mode=service.mode,
        enabled=service.enabled,
        vip_pps=service.vip_pps,
        vip_bps=service.vip_bps,
        service_pps=service.service_pps,
        service_bps=service.service_bps,
        plan=service.plan,
        rules=tuple(sorted(service.rules, key=lambda rule: rule.priority)),
        whitelist=tuple(sorted(service.whitelist_entries, key=lambda entry: entry.source_cidr)),
        blacklist=tuple(sorted(service.blacklist_entries, key=lambda entry: entry.source_cidr)),
    )


def _cidr_parts(cidr: str) -> tuple[int, bytes]:
    network = ipaddress.ip_network(cidr, strict=False)
    if network.version != 4:
        raise ValueError(f"apply snapshots require IPv4 CIDRs: {cidr}")
    return network.prefixlen, network.network_address.packed


def _strict_ipv4_cidr_parts(cidr: str) -> tuple[int, bytes]:
    network = ipaddress.ip_network(cidr, strict=True)
    if network.version != 4:
        raise ValueError(f"global deny snapshots require IPv4 CIDRs: {cidr}")
    return network.prefixlen, network.network_address.packed


def _plan_rates(plan: ServicePlan | None) -> tuple[int, int]:
    if plan is None:
        return 0, 0
    return (
        int(plan.committed_clean_gbps * _GBPS),
        int(plan.ceiling_clean_gbps * _GBPS),
    )


def _vip_flags(service: ServiceConfig) -> int:
    return (_VIP_F_PPS_SET if service.vip_pps is not None else 0) | (
        _VIP_F_BPS_SET if service.vip_bps is not None else 0
    )


def _svc_rl_flags(service: ServiceConfig) -> int:
    return (_SVC_RL_F_PPS_SET if service.service_pps is not None else 0) | (
        _SVC_RL_F_BPS_SET if service.service_bps is not None else 0
    )


def _rule_flags(rule: AllowRule) -> int:
    # The v2 apply wire format (apply_snapshot.h, APPLY_SNAPSHOT_RULE_SIZE == 10)
    # carries only src/dst ports, proto and flags per rule -- it has NO field for
    # per-rule pps/bps values. Emitting RULE_F_PPS_SET / RULE_F_BPS_SET here would
    # make the data plane enforce a token bucket that is seeded with zero tokens
    # (rl_bucket_consume never admits), silently black-holing 100% of the rule's
    # traffic as rate_limit_drop. Until the wire format is extended to carry the
    # values, per-rule rate limits are NOT enforced and the flags must stay clear.
    return _RULE_F_ENABLED if rule.enabled else 0


def _list_flags(entries: tuple[tuple[int, bytes], ...], *, active: bool) -> int:
    if not active:
        return 0
    has_broad_entry = any(prefix < _BLOOM_PREFIX for prefix, _ in entries)
    return _WL_F_ACTIVE | (_WL_F_HAS_BROAD if has_broad_entry else 0)


def _append_source_list(payload: bytearray, entries: tuple[tuple[int, bytes], ...]) -> None:
    payload.extend(struct.pack("<I", len(entries)))
    for prefixlen, address in entries:
        payload.extend(struct.pack("<I4s", prefixlen, address))


def _stderr_text(stderr: bytes) -> str:
    return stderr.decode(errors="replace").strip()


def _parse_global_apply_result(stdout: bytes) -> GlobalDenyApplyResult:
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApplyError("xdpgw-apply returned malformed global apply result") from exc
    if not isinstance(payload, dict) or set(payload) != {"active_slot", "node_map_version"}:
        raise ApplyError("xdpgw-apply returned malformed global apply result")

    active_slot = payload["active_slot"]
    node_map_version = payload["node_map_version"]
    if (
        isinstance(active_slot, bool)
        or not isinstance(active_slot, int)
        or active_slot not in {0, 1}
        or isinstance(node_map_version, bool)
        or not isinstance(node_map_version, int)
        or node_map_version < 0
    ):
        raise ApplyError("xdpgw-apply returned malformed global apply result")
    return GlobalDenyApplyResult(
        active_slot=active_slot,
        node_map_version=node_map_version,
    )
