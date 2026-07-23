import logging
import uuid
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AllowRule,
    BlacklistEntry,
    BlacklistScope,
    BlacklistSource,
    ProtectedService,
    Protocol,
    ServiceMode,
    ServicePlan,
    Tenant,
    WhitelistEntry,
)
from app.worker.applier import PlaceholderApplier, load_service_config

pytestmark = pytest.mark.integration


async def create_service(
    db_session: AsyncSession,
    *,
    name: str,
    cidr_or_ip: str,
    version: int = 1,
) -> tuple[ProtectedService, ServicePlan]:
    tenant = Tenant(name=f"{name} tenant")
    db_session.add(tenant)
    await db_session.flush()

    service = ProtectedService(
        tenant_id=tenant.id,
        name=name,
        cidr_or_ip=cidr_or_ip,
        mode=ServiceMode.allow_rule_only,
        enabled=True,
        vip_pps=1_000,
        vip_bps=2_000,
        version=version,
    )
    plan = ServicePlan(
        service=service,
        committed_clean_gbps=Decimal("2"),
        ceiling_clean_gbps=Decimal("5"),
    )
    db_session.add_all([service, plan])
    await db_session.flush()
    return service, plan


async def test_load_service_config_snapshots_populated_service(db_session: AsyncSession) -> None:
    service, plan = await create_service(
        db_session,
        name="populated-service",
        cidr_or_ip="203.0.113.10/32",
        version=7,
    )
    db_session.add_all(
        [
            AllowRule(service_id=service.id, priority=10, protocol=Protocol.tcp),
            AllowRule(service_id=service.id, priority=20, protocol=Protocol.udp),
            WhitelistEntry(service_id=service.id, source_cidr="198.51.100.7/32"),
        ]
    )
    await db_session.flush()

    config = await load_service_config(db_session, service.id)

    assert config is not None
    assert config.service_id == service.id
    assert config.version == 7
    assert config.name == "populated-service"
    assert str(config.cidr_or_ip) == "203.0.113.10/32"
    assert config.mode == ServiceMode.allow_rule_only
    assert config.enabled is True
    assert config.vip_pps == 1_000
    assert config.vip_bps == 2_000
    assert config.plan is not None
    assert config.plan.id == plan.id
    assert len(config.rules) == 2
    assert len(config.whitelist) == 1
    assert isinstance(config.rules, tuple)
    with pytest.raises(FrozenInstanceError):
        config.version = 8


async def test_load_service_config_snapshots_empty_child_collections(
    db_session: AsyncSession,
) -> None:
    service, plan = await create_service(
        db_session,
        name="empty-service",
        cidr_or_ip="203.0.113.11/32",
    )

    config = await load_service_config(db_session, service.id)

    assert config is not None
    assert config.plan is not None
    assert config.plan.id == plan.id
    assert config.rules == ()
    assert config.whitelist == ()


async def test_load_service_config_returns_none_for_missing_service(
    db_session: AsyncSession,
) -> None:
    assert await load_service_config(db_session, uuid.uuid4()) is None


async def test_placeholder_applier_logs_and_succeeds(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = await create_service(
        db_session,
        name="placeholder-service",
        cidr_or_ip="203.0.113.12/32",
        version=3,
    )
    config = await load_service_config(db_session, service.id)
    assert config is not None

    monkeypatch.setattr(logging.getLogger("app.worker.applier"), "disabled", False)
    caplog.set_level(logging.INFO)
    await PlaceholderApplier().apply(config)

    assert "placeholder apply acknowledged" in caplog.text
    record = caplog.records[-1]
    assert record.service_id == str(service.id)
    assert record.version == 3
    assert record.rule_count == 0
    assert record.whitelist_count == 0
