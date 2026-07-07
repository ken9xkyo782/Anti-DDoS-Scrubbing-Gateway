import asyncio
import uuid

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AllocatedCIDR, CIDRStatus, Role, Tenant, User
from app.db.session import dispose_engine, get_session_factory

pytestmark = pytest.mark.integration


async def test_migration_creates_named_exclusion_constraint(
    db_session: AsyncSession,
) -> None:
    constraint_name = (
        await db_session.execute(
            text(
                """
                SELECT conname
                FROM pg_constraint
                JOIN pg_class ON pg_constraint.conrelid = pg_class.oid
                WHERE pg_class.relname = 'allocated_cidr'
                  AND conname = 'allocated_cidr_active_no_overlap'
                """
            )
        )
    ).scalar_one_or_none()

    assert constraint_name == "allocated_cidr_active_no_overlap"


async def test_active_allocations_cannot_overlap(db_session: AsyncSession) -> None:
    tenant = Tenant(name="Overlap Tenant")
    other = Tenant(name="Overlap Other")
    db_session.add_all([tenant, other])
    await db_session.flush()

    db_session.add(
        AllocatedCIDR(
            tenant_id=tenant.id,
            cidr="203.0.113.0/24",
            status=CIDRStatus.active,
        )
    )
    await db_session.flush()

    db_session.add(
        AllocatedCIDR(
            tenant_id=other.id,
            cidr="203.0.113.128/25",
            status=CIDRStatus.active,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_revoked_allocations_do_not_block_reallocation(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(name="Revoked Tenant")
    other = Tenant(name="Revoked Other")
    db_session.add_all([tenant, other])
    await db_session.flush()

    db_session.add(
        AllocatedCIDR(
            tenant_id=tenant.id,
            cidr="198.51.100.0/24",
            status=CIDRStatus.revoked,
        )
    )
    db_session.add(
        AllocatedCIDR(
            tenant_id=other.id,
            cidr="198.51.100.0/24",
            status=CIDRStatus.active,
        )
    )

    await db_session.flush()


async def test_tenant_name_is_unique_case_insensitive(db_session: AsyncSession) -> None:
    db_session.add(Tenant(name="Acme"))
    await db_session.flush()

    db_session.add(Tenant(name="acme"))

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_deleting_tenant_with_active_cidr_is_restricted(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(name="Restricted CIDR Tenant")
    db_session.add(tenant)
    await db_session.flush()

    db_session.add(
        AllocatedCIDR(
            tenant_id=tenant.id,
            cidr="192.0.2.0/24",
            status=CIDRStatus.active,
        )
    )
    await db_session.flush()

    await db_session.delete(tenant)

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_deleting_tenant_with_user_is_restricted(db_session: AsyncSession) -> None:
    tenant = Tenant(name="Restricted User Tenant")
    user = User(
        username="restricted-user",
        role=Role.tenant_user,
        tenant=tenant,
        password_hash="$argon2id$hash",
    )
    db_session.add(user)
    await db_session.flush()

    await db_session.delete(tenant)

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_concurrent_overlapping_active_inserts_allow_exactly_one_commit() -> None:
    session_factory = get_session_factory()
    async with session_factory() as setup:
        tenant = Tenant(name="Concurrent CIDR Tenant A")
        other = Tenant(name="Concurrent CIDR Tenant B")
        setup.add_all([tenant, other])
        await setup.commit()
        tenant_ids = [tenant.id, other.id]

    async def insert_allocation(tenant_id: uuid.UUID) -> bool:
        async with session_factory() as session:
            session.add(
                AllocatedCIDR(
                    tenant_id=tenant_id,
                    cidr="172.31.0.0/24",
                    status=CIDRStatus.active,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
            return True

    try:
        results = await asyncio.gather(*(insert_allocation(tenant_id) for tenant_id in tenant_ids))
        assert sorted(results) == [False, True]
    finally:
        async with session_factory() as cleanup:
            await cleanup.execute(
                delete(AllocatedCIDR).where(AllocatedCIDR.tenant_id.in_(tenant_ids))
            )
            await cleanup.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
            await cleanup.commit()
        await dispose_engine()

    async with session_factory() as verify:
        remaining = (
            await verify.execute(
                select(AllocatedCIDR).where(AllocatedCIDR.tenant_id.in_(tenant_ids))
            )
        ).scalars()
        assert list(remaining) == []
