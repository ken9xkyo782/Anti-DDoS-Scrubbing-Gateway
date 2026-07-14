import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    BillingSample,
    BillingStatus,
    BillingUsage,
    OveragePolicy,
    ProtectedService,
    Tenant,
)
from app.db.session import dispose_engine, get_session_factory

pytestmark = pytest.mark.integration


async def create_service(
    db_session: AsyncSession,
    *,
    name: str = "billing-edge",
    cidr_or_ip: str = "203.0.113.10/32",
) -> ProtectedService:
    tenant = Tenant(name=f"Billing Model Tenant {name}")
    service = ProtectedService(tenant=tenant, name=name, cidr_or_ip=cidr_or_ip)
    db_session.add_all([tenant, service])
    await db_session.flush()
    return service


def billing_usage(
    *,
    service: ProtectedService | None,
    period_start: datetime,
    period_end: datetime,
) -> BillingUsage:
    return BillingUsage(
        service_id=service.id if service is not None else None,
        tenant_id=service.tenant_id if service is not None else None,
        service_name=service.name if service is not None else "deleted-billing-edge",
        period_start=period_start,
        period_end=period_end,
        billing_metric="p95_clean_bps",
        committed_clean_gbps=Decimal("1.00"),
        p95_clean_gbps=Decimal("1.25"),
        billed_gbps=Decimal("1.25"),
        overage_gbps=Decimal("0.25"),
        overage_policy=OveragePolicy.billed,
        sample_count=12,
        status=BillingStatus.open,
    )


async def test_billing_sample_unique_service_timestamp(db_session: AsyncSession) -> None:
    service = await create_service(db_session)
    sample_ts = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    db_session.add(
        BillingSample(
            service_id=service.id,
            dp_id=service.dp_id,
            sample_ts=sample_ts,
            clean_bps=1_250_000_000,
            window_seconds=300,
            is_reset=False,
        )
    )
    await db_session.flush()

    async with db_session.begin_nested():
        db_session.add(
            BillingSample(
                service_id=service.id,
                dp_id=service.dp_id,
                sample_ts=sample_ts,
                clean_bps=1_250_000_000,
                window_seconds=300,
                is_reset=False,
            )
        )
        with pytest.raises(IntegrityError) as exc_info:
            await db_session.flush()

    assert "uq_billing_sample_service_ts" in str(exc_info.value)


async def test_billing_usage_unique_service_period(db_session: AsyncSession) -> None:
    service = await create_service(db_session)
    period_start = datetime(2026, 7, 1, tzinfo=UTC)
    period_end = datetime(2026, 8, 1, tzinfo=UTC)
    db_session.add(billing_usage(service=service, period_start=period_start, period_end=period_end))
    await db_session.flush()

    async with db_session.begin_nested():
        db_session.add(
            billing_usage(service=service, period_start=period_start, period_end=period_end)
        )
        with pytest.raises(IntegrityError) as exc_info:
            await db_session.flush()

    assert "uq_billing_usage_service_period" in str(exc_info.value)


async def test_deleting_service_cascades_billing_samples(db_session: AsyncSession) -> None:
    service = await create_service(db_session)
    db_session.add(
        BillingSample(
            service_id=service.id,
            dp_id=service.dp_id,
            sample_ts=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
            clean_bps=0,
            window_seconds=300,
            is_reset=False,
        )
    )
    await db_session.flush()

    await db_session.delete(service)
    await db_session.flush()

    assert (await db_session.scalar(select(func.count(BillingSample.id)))) == 0


async def test_deleting_service_preserves_billing_usage_and_nulls_service_id(
    db_session: AsyncSession,
) -> None:
    service = await create_service(db_session)
    usage = billing_usage(
        service=service,
        period_start=datetime(2026, 7, 1, tzinfo=UTC),
        period_end=datetime(2026, 8, 1, tzinfo=UTC),
    )
    db_session.add(usage)
    await db_session.flush()

    await db_session.delete(service)
    await db_session.flush()
    await db_session.refresh(usage)

    assert usage.service_id is None
    assert usage.tenant_id is not None
    assert usage.service_name == "billing-edge"


async def test_billing_usage_enum_round_trip_and_schema_indexes(db_session: AsyncSession) -> None:
    service = await create_service(db_session)
    usage = billing_usage(
        service=service,
        period_start=datetime(2026, 7, 1, tzinfo=UTC),
        period_end=datetime(2026, 8, 1, tzinfo=UTC),
    )
    usage.status = BillingStatus.final
    usage.finalized_at = datetime(2026, 8, 1, tzinfo=UTC)
    db_session.add(usage)
    await db_session.flush()
    await db_session.refresh(usage)

    assert [status.value for status in BillingStatus] == ["open", "final"]
    assert usage.status is BillingStatus.final
    assert BillingUsage.__table__.c.status.type.native_enum is False
    assert {index.name for index in BillingSample.__table__.indexes} == {
        "ix_billing_sample_service_ts"
    }
    assert {index.name for index in BillingUsage.__table__.indexes} == {
        "ix_billing_usage_status_end",
        "ix_billing_usage_tenant_period",
    }


async def test_null_service_usage_rows_share_a_period(db_session: AsyncSession) -> None:
    period_start = datetime(2026, 7, 1, tzinfo=UTC)
    period_end = datetime(2026, 8, 1, tzinfo=UTC)
    db_session.add_all(
        [
            billing_usage(service=None, period_start=period_start, period_end=period_end),
            billing_usage(service=None, period_start=period_start, period_end=period_end),
        ]
    )

    await db_session.flush()

    assert (await db_session.scalar(select(func.count(BillingUsage.id)))) == 2


async def test_billing_migration_upgrades_and_downgrades_cleanly(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    del committed_db
    config = Config("alembic.ini")
    await dispose_engine()
    await asyncio.to_thread(downgrade, config, "20260710_0008")
    try:
        session_factory = get_session_factory()
        async with session_factory() as db_session:
            tables_before_upgrade = (
                (
                    await db_session.execute(
                        text(
                            "SELECT tablename FROM pg_tables "
                            "WHERE schemaname = 'public' "
                            "AND tablename IN ('billing_sample', 'billing_usage')"
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
                            "AND tablename IN ('billing_sample', 'billing_usage') "
                            "ORDER BY tablename"
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert tables_after_upgrade == ["billing_sample", "billing_usage"]
    finally:
        await dispose_engine()
        await asyncio.to_thread(upgrade, config, "head")
