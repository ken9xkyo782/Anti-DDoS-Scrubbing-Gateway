import asyncio
from datetime import UTC, datetime

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    NodeHealthSnapshot,
    ProtectedService,
    TelemetryCounter,
    TelemetryScope,
    Tenant,
    XdpMode,
)
from app.db.session import dispose_engine, get_session_factory

pytestmark = pytest.mark.integration


async def create_service(db_session: AsyncSession) -> ProtectedService:
    tenant = Tenant(name="Telemetry Model Tenant")
    service = ProtectedService(
        tenant=tenant,
        name="telemetry-edge",
        cidr_or_ip="203.0.113.10/32",
    )
    db_session.add_all([tenant, service])
    await db_session.flush()
    return service


async def test_telemetry_rows_round_trip_jsonb_and_retain_history_after_service_delete(
    db_session: AsyncSession,
) -> None:
    service = await create_service(db_session)
    window_start = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    service_counter = TelemetryCounter(
        scope=TelemetryScope.service,
        service_id=service.id,
        dp_id=service.dp_id,
        window_start=window_start,
        window_seconds=2,
        clean_pkts=101,
        clean_bytes=12_345,
        drop_pkts=7,
        drop_bytes=456,
        drop_by_reason={"blacklist_drop": 7},
        pps=50,
        bps=6_172,
        top_dst_ports=[{"port": 443, "count": 99}],
        top_src=[{"ip": "198.51.100.10", "count": 88}],
        is_baseline=True,
    )
    node_counter = TelemetryCounter(
        scope=TelemetryScope.node,
        window_start=window_start,
        window_seconds=2,
        clean_pkts=1_001,
        clean_bytes=123_456,
        drop_pkts=70,
        drop_bytes=4_560,
        drop_by_reason={"blacklist_drop": 70},
        pps=500,
        bps=61_728,
    )
    health = NodeHealthSnapshot(
        captured_at=window_start,
        window_seconds=2,
        xdp_mode=XdpMode.native,
        active_slot=1,
        map_version=42,
        map_error_count=3,
        node_clean_bps=61_728,
        node_capacity_bps=1_000_000_000,
        bloom_stats={"global": {"hits": 9, "false_positives": 1}},
    )
    db_session.add_all([service_counter, node_counter, health])
    await db_session.flush()

    await db_session.refresh(service_counter)
    await db_session.refresh(node_counter)
    await db_session.refresh(health)

    assert service_counter.scope is TelemetryScope.service
    assert service_counter.service_id == service.id
    assert service_counter.dp_id == service.dp_id
    assert service_counter.drop_by_reason == {"blacklist_drop": 7}
    assert service_counter.top_dst_ports == [{"port": 443, "count": 99}]
    assert service_counter.top_src == [{"ip": "198.51.100.10", "count": 88}]
    assert service_counter.is_baseline is True
    assert service_counter.created_at is not None
    assert node_counter.scope is TelemetryScope.node
    assert node_counter.service_id is None
    assert node_counter.dp_id is None
    assert node_counter.top_dst_ports is None
    assert node_counter.top_src is None
    assert health.xdp_mode is XdpMode.native
    assert health.bloom_stats == {"global": {"hits": 9, "false_positives": 1}}

    await db_session.delete(service)
    await db_session.flush()
    await db_session.refresh(service_counter)

    assert service_counter.service_id is None
    assert service_counter.dp_id is not None


async def test_telemetry_models_expose_native_false_enums_and_required_indexes(
    db_session: AsyncSession,
) -> None:
    del db_session

    assert [scope.value for scope in TelemetryScope] == ["service", "node"]
    assert [mode.value for mode in XdpMode] == ["native", "generic", "offline", "unknown"]
    assert TelemetryCounter.__table__.c.scope.type.native_enum is False
    assert NodeHealthSnapshot.__table__.c.xdp_mode.type.native_enum is False
    assert {
        index.name: str(index.expressions[-1]) for index in TelemetryCounter.__table__.indexes
    } == {
        "ix_telemetry_counter_scope_service_window_start": "window_start DESC",
        "ix_telemetry_counter_scope_window_start": "window_start DESC",
    }
    assert {
        index.name: str(index.expressions[-1]) for index in NodeHealthSnapshot.__table__.indexes
    } == {"ix_node_health_snapshot_captured_at": "captured_at DESC"}
    assert next(iter(TelemetryCounter.__table__.foreign_keys)).ondelete == "SET NULL"


async def test_telemetry_migration_upgrades_and_downgrades_cleanly(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    del committed_db
    config = Config("alembic.ini")
    await dispose_engine()
    await asyncio.to_thread(downgrade, config, "20260710_0007")
    try:
        session_factory = get_session_factory()
        async with session_factory() as db_session:
            tables_before_upgrade = (
                (
                    await db_session.execute(
                        text(
                            "SELECT tablename FROM pg_tables "
                            "WHERE schemaname = 'public' "
                            "AND tablename IN ('telemetry_counter', 'node_health_snapshot')"
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert tables_before_upgrade == []

        await dispose_engine()
        await asyncio.to_thread(upgrade, config, "head")

        session_factory = get_session_factory()
        async with session_factory() as db_session:
            tables_after_upgrade = (
                (
                    await db_session.execute(
                        text(
                            "SELECT tablename FROM pg_tables "
                            "WHERE schemaname = 'public' "
                            "AND tablename IN ('telemetry_counter', 'node_health_snapshot') "
                            "ORDER BY tablename"
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert tables_after_upgrade == ["node_health_snapshot", "telemetry_counter"]
    finally:
        await dispose_engine()
        await asyncio.to_thread(upgrade, config, "head")
