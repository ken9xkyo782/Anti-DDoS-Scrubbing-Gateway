from ipaddress import IPv4Network

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AllowRule, AuditEvent, Protocol, Role, Tenant, User
from app.services import allocations as allocation_service
from app.services import rules as rule_service
from app.services import services as service_service

pytestmark = pytest.mark.integration


async def create_admin(db_session: AsyncSession, username: str = "rule-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash="$argon2id$hash")
    db_session.add(user)
    await db_session.flush()
    return user


async def create_tenant(db_session: AsyncSession, name: str) -> Tenant:
    tenant = Tenant(name=name)
    db_session.add(tenant)
    await db_session.flush()
    return tenant


async def create_tenant_user(
    db_session: AsyncSession,
    *,
    username: str,
    tenant: Tenant,
) -> User:
    user = User(
        username=username,
        role=Role.tenant_user,
        tenant=tenant,
        password_hash="$argon2id$hash",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def create_service(
    db_session: AsyncSession,
    *,
    tenant: Tenant,
    actor: User,
    name: str = "rule-service",
    cidr: str = "203.0.113.10/32",
) -> service_service.ServiceRecord:
    await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("203.0.113.0/24"),
        actor=actor,
    )
    return await service_service.create_service(
        db_session,
        tenant_id=tenant.id,
        name=name,
        cidr_or_ip=IPv4Network(cidr),
        actor=actor,
    )


async def test_create_rule_persists_bumps_version_and_audits(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session, "Rule Create Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    result = await rule_service.create_rule(
        db_session,
        service_id=service.service.id,
        actor=admin,
        priority=10,
        protocol=Protocol.tcp,
        dst_port_lo=80,
        dst_port_hi=80,
    )

    assert result.rule.id is not None
    assert result.warnings == []
    assert service.service.version == 2
    audit = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "rule.create"))
    ).scalar_one()
    assert audit.target_id == str(result.rule.id)


async def test_duplicate_priority_returns_409(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "rule-duplicate-admin")
    tenant = await create_tenant(db_session, "Rule Duplicate Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)
    await rule_service.create_rule(
        db_session,
        service_id=service.service.id,
        actor=admin,
        priority=10,
        protocol=Protocol.tcp,
    )

    with pytest.raises(HTTPException) as exc_info:
        await rule_service.create_rule(
            db_session,
            service_id=service.service.id,
            actor=admin,
            priority=10,
            protocol=Protocol.udp,
        )

    assert exc_info.value.status_code == 409


async def test_seventeenth_rule_returns_409(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "rule-limit-admin")
    tenant = await create_tenant(db_session, "Rule Limit Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)
    for priority in range(1, 17):
        await rule_service.create_rule(
            db_session,
            service_id=service.service.id,
            actor=admin,
            priority=priority,
            protocol=Protocol.tcp,
        )

    with pytest.raises(HTTPException) as exc_info:
        await rule_service.create_rule(
            db_session,
            service_id=service.service.id,
            actor=admin,
            priority=17,
            protocol=Protocol.tcp,
        )

    assert exc_info.value.status_code == 409


async def test_invalid_port_range_returns_422(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "rule-invalid-port-admin")
    tenant = await create_tenant(db_session, "Rule Invalid Port Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    with pytest.raises(HTTPException) as exc_info:
        await rule_service.create_rule(
            db_session,
            service_id=service.service.id,
            actor=admin,
            priority=10,
            protocol=Protocol.tcp,
            dst_port_lo=80,
            dst_port_hi=79,
        )

    assert exc_info.value.status_code == 422


async def test_overlapping_rule_succeeds_with_warning(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "rule-overlap-admin")
    tenant = await create_tenant(db_session, "Rule Overlap Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)
    await rule_service.create_rule(
        db_session,
        service_id=service.service.id,
        actor=admin,
        priority=10,
        protocol=Protocol.tcp,
        dst_port_lo=80,
        dst_port_hi=80,
    )

    result = await rule_service.create_rule(
        db_session,
        service_id=service.service.id,
        actor=admin,
        priority=20,
        protocol=Protocol.any,
        dst_port_lo=None,
        dst_port_hi=None,
    )

    assert result.rule.priority == 20
    assert result.warnings == ["Overlaps rule priority 10"]


async def test_overlap_dry_run_reports_without_writing(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "rule-dry-run-admin")
    tenant = await create_tenant(db_session, "Rule Dry Run Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)
    await rule_service.create_rule(
        db_session,
        service_id=service.service.id,
        actor=admin,
        priority=10,
        protocol=Protocol.tcp,
        dst_port_lo=80,
        dst_port_hi=80,
    )
    before = (await db_session.execute(select(func.count(AllowRule.id)))).scalar_one()

    warnings = await rule_service.overlap_dry_run(
        db_session,
        service_id=service.service.id,
        actor=admin,
        protocol=Protocol.tcp,
        dst_port_lo=80,
        dst_port_hi=443,
    )
    after = (await db_session.execute(select(func.count(AllowRule.id)))).scalar_one()

    assert warnings == ["Overlaps rule priority 10"]
    assert after == before


async def test_list_get_update_and_delete_rule_are_scoped_and_audited(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "rule-crud-admin")
    tenant = await create_tenant(db_session, "Rule CRUD Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)
    created = await rule_service.create_rule(
        db_session,
        service_id=service.service.id,
        actor=admin,
        priority=10,
        protocol=Protocol.tcp,
    )

    listed = await rule_service.list_rules(db_session, service_id=service.service.id, actor=admin)
    loaded = await rule_service.get_rule(
        db_session,
        service_id=service.service.id,
        rule_id=created.rule.id,
        actor=admin,
    )
    updated = await rule_service.update_rule(
        db_session,
        service_id=service.service.id,
        rule_id=created.rule.id,
        actor=admin,
        priority=20,
        protocol=Protocol.udp,
    )
    await rule_service.delete_rule(
        db_session,
        service_id=service.service.id,
        rule_id=created.rule.id,
        actor=admin,
    )

    assert [rule.id for rule in listed] == [created.rule.id]
    assert loaded.id == created.rule.id
    assert updated.rule.priority == 20
    assert (await db_session.execute(select(func.count(AllowRule.id)))).scalar_one() == 0
    actions = (
        (
            await db_session.execute(
                select(AuditEvent.action)
                .where(AuditEvent.action.in_(["rule.update", "rule.delete"]))
                .order_by(AuditEvent.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert actions == ["rule.update", "rule.delete"]


async def test_update_rule_duplicate_priority_returns_409(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "rule-update-duplicate-admin")
    tenant = await create_tenant(db_session, "Rule Update Duplicate Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)
    first = await rule_service.create_rule(
        db_session,
        service_id=service.service.id,
        actor=admin,
        priority=10,
        protocol=Protocol.tcp,
    )
    second = await rule_service.create_rule(
        db_session,
        service_id=service.service.id,
        actor=admin,
        priority=20,
        protocol=Protocol.udp,
    )

    with pytest.raises(HTTPException) as exc_info:
        await rule_service.update_rule(
            db_session,
            service_id=service.service.id,
            rule_id=second.rule.id,
            actor=admin,
            priority=first.rule.priority,
        )

    assert exc_info.value.status_code == 409


async def test_foreign_tenant_actor_cannot_mutate_rules(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "rule-foreign-admin")
    own_tenant = await create_tenant(db_session, "Rule Own Tenant")
    other_tenant = await create_tenant(db_session, "Rule Other Tenant")
    foreign_user = await create_tenant_user(
        db_session,
        username="rule-foreign-user",
        tenant=other_tenant,
    )
    service = await create_service(db_session, tenant=own_tenant, actor=admin)

    with pytest.raises(HTTPException) as exc_info:
        await rule_service.create_rule(
            db_session,
            service_id=service.service.id,
            actor=foreign_user,
            priority=10,
            protocol=Protocol.tcp,
        )

    assert exc_info.value.status_code == 403
