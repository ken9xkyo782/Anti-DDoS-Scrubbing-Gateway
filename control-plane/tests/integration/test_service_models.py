from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AllowRule,
    BlacklistEntry,
    BlacklistScope,
    BlacklistSource,
    ProtectedService,
    Protocol,
    Role,
    ServicePlan,
    Tenant,
    User,
    WhitelistEntry,
)

pytestmark = pytest.mark.integration


async def create_admin(db_session: AsyncSession, username: str = "service-model-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash="$argon2id$hash")
    db_session.add(user)
    await db_session.flush()
    return user


async def create_tenant(db_session: AsyncSession, name: str = "Service Model Tenant") -> Tenant:
    tenant = Tenant(name=name)
    db_session.add(tenant)
    await db_session.flush()
    return tenant


async def create_service(
    db_session: AsyncSession,
    *,
    tenant: Tenant,
    name: str,
    cidr_or_ip: str = "203.0.113.10/32",
    actor: User | None = None,
) -> ProtectedService:
    service = ProtectedService(
        tenant_id=tenant.id,
        name=name,
        cidr_or_ip=cidr_or_ip,
        created_by=actor.id if actor is not None else None,
    )
    db_session.add(service)
    await db_session.flush()
    return service


async def test_migration_creates_service_tables_and_dest_constraint(
    db_session: AsyncSession,
) -> None:
    tables = (
        await db_session.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN (
                    'protected_service',
                    'service_plan',
                    'allow_rule',
                    'whitelist_entry',
                    'blacklist_entry'
                  )
                ORDER BY table_name
                """
            )
        )
    ).scalars()
    constraint_name = (
        await db_session.execute(
            text(
                """
                SELECT conname
                FROM pg_constraint
                JOIN pg_class ON pg_class.oid = pg_constraint.conrelid
                WHERE pg_class.relname = 'protected_service'
                  AND conname = 'protected_service_dest_no_overlap'
                """
            )
        )
    ).scalar_one()

    assert list(tables) == [
        "allow_rule",
        "blacklist_entry",
        "protected_service",
        "service_plan",
        "whitelist_entry",
    ]
    assert constraint_name == "protected_service_dest_no_overlap"


async def test_overlapping_service_destination_violates_even_when_disabled(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session, "service-overlap-admin")
    tenant = await create_tenant(db_session, "Service Overlap Tenant")
    await create_service(
        db_session,
        tenant=tenant,
        actor=actor,
        name="edge",
        cidr_or_ip="203.0.113.0/24",
    )

    with pytest.raises(IntegrityError) as exc_info:
        await create_service(
            db_session,
            tenant=tenant,
            actor=actor,
            name="nested",
            cidr_or_ip="203.0.113.128/25",
        )

    assert "protected_service_dest_no_overlap" in str(exc_info.value)


async def test_delete_service_frees_destination_range(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session, "service-delete-free-admin")
    tenant = await create_tenant(db_session, "Service Delete Free Tenant")
    service = await create_service(
        db_session,
        tenant=tenant,
        actor=actor,
        name="old-edge",
        cidr_or_ip="198.51.100.0/24",
    )
    await db_session.delete(service)
    await db_session.flush()

    replacement = await create_service(
        db_session,
        tenant=tenant,
        actor=actor,
        name="new-edge",
        cidr_or_ip="198.51.100.128/25",
    )

    assert replacement.id is not None


async def test_service_plan_committed_cannot_exceed_ceiling(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session, "service-plan-check-admin")
    tenant = await create_tenant(db_session, "Service Plan Check Tenant")
    service = await create_service(db_session, tenant=tenant, actor=actor, name="planned")
    db_session.add(
        ServicePlan(
            service_id=service.id,
            committed_clean_gbps=Decimal("5"),
            ceiling_clean_gbps=Decimal("2"),
        )
    )

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()

    assert "ck_service_plan_committed_le_ceiling" in str(exc_info.value)


async def test_service_plan_accepts_equal_and_zero_values(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session, "service-plan-valid-admin")
    tenant = await create_tenant(db_session, "Service Plan Valid Tenant")
    equal_service = await create_service(
        db_session,
        tenant=tenant,
        actor=actor,
        name="equal-plan",
        cidr_or_ip="10.200.0.10/32",
    )
    zero_service = await create_service(
        db_session,
        tenant=tenant,
        actor=actor,
        name="zero-plan",
        cidr_or_ip="10.200.0.20/32",
    )
    db_session.add_all(
        [
            ServicePlan(
                service_id=equal_service.id,
                committed_clean_gbps=Decimal("5"),
                ceiling_clean_gbps=Decimal("5"),
            ),
            ServicePlan(
                service_id=zero_service.id,
                committed_clean_gbps=Decimal("0"),
                ceiling_clean_gbps=Decimal("0"),
            ),
        ]
    )

    await db_session.flush()

    count = (await db_session.execute(select(func.count(ServicePlan.id)))).scalar_one()
    assert count == 2


async def test_duplicate_rule_priority_violates_unique_constraint(
    db_session: AsyncSession,
) -> None:
    actor = await create_admin(db_session, "rule-priority-admin")
    tenant = await create_tenant(db_session, "Rule Priority Tenant")
    service = await create_service(db_session, tenant=tenant, actor=actor, name="rules")
    db_session.add_all(
        [
            AllowRule(service_id=service.id, priority=10, protocol=Protocol.tcp),
            AllowRule(service_id=service.id, priority=10, protocol=Protocol.udp),
        ]
    )

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()

    assert "uq_allow_rule_service_priority" in str(exc_info.value)


async def test_invalid_rule_port_range_violates_check(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session, "rule-port-admin")
    tenant = await create_tenant(db_session, "Rule Port Tenant")
    service = await create_service(db_session, tenant=tenant, actor=actor, name="ports")
    db_session.add(
        AllowRule(
            service_id=service.id,
            priority=10,
            protocol=Protocol.tcp,
            dst_port_lo=80,
            dst_port_hi=79,
        )
    )

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()

    assert "ck_allow_rule_dst_port_range" in str(exc_info.value)


async def test_delete_service_cascades_rules_and_lists(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session, "service-cascade-admin")
    tenant = await create_tenant(db_session, "Service Cascade Tenant")
    service = await create_service(db_session, tenant=tenant, actor=actor, name="cascade")
    db_session.add_all(
        [
            AllowRule(service_id=service.id, priority=10, protocol=Protocol.tcp),
            WhitelistEntry(
                service_id=service.id,
                source_cidr="198.51.100.7/32",
                created_by=actor.id,
            ),
            BlacklistEntry(
                service_id=service.id,
                scope=BlacklistScope.service,
                source=BlacklistSource.manual,
                source_cidr="45.0.0.0/8",
                created_by=actor.id,
            ),
        ]
    )
    await db_session.flush()

    await db_session.delete(service)
    await db_session.flush()

    assert (await db_session.execute(select(func.count(AllowRule.id)))).scalar_one() == 0
    assert (await db_session.execute(select(func.count(WhitelistEntry.id)))).scalar_one() == 0
    assert (await db_session.execute(select(func.count(BlacklistEntry.id)))).scalar_one() == 0


async def test_blacklist_scope_service_id_xor_constraint(db_session: AsyncSession) -> None:
    actor = await create_admin(db_session, "blacklist-xor-admin")
    tenant = await create_tenant(db_session, "Blacklist XOR Tenant")
    service = await create_service(db_session, tenant=tenant, actor=actor, name="blacklist")
    db_session.add(
        BlacklistEntry(
            service_id=None,
            scope=BlacklistScope.global_,
            source=BlacklistSource.manual,
            source_cidr="185.0.0.0/8",
            created_by=actor.id,
        )
    )
    await db_session.flush()

    db_session.add(
        BlacklistEntry(
            service_id=None,
            scope=BlacklistScope.service,
            source=BlacklistSource.manual,
            source_cidr="198.51.100.7/32",
            created_by=actor.id,
        )
    )

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()

    assert "ck_blacklist_scope_service_id" in str(exc_info.value)
    assert service.id is not None
