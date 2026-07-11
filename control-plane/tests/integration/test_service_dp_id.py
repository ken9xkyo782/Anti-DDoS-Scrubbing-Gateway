import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from ipaddress import IPv4Network

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Role, Tenant, User
from app.db.session import dispose_engine, get_session_factory
from app.services import allocations as allocation_service
from app.services import services as service_service

pytestmark = pytest.mark.integration


async def create_admin(db_session: AsyncSession) -> User:
    admin = User(username="dp-id-admin", role=Role.admin, password_hash="$argon2id$hash")
    db_session.add(admin)
    await db_session.flush()
    return admin


async def create_service(
    db_session: AsyncSession,
    *,
    tenant: Tenant,
    actor: User,
    name: str,
    cidr: str,
) -> service_service.ServiceRecord:
    return await service_service.create_service(
        db_session,
        tenant_id=tenant.id,
        name=name,
        cidr_or_ip=IPv4Network(cidr),
        actor=actor,
        committed_clean_gbps=Decimal("0"),
        ceiling_clean_gbps=Decimal("0"),
    )


async def test_create_service_assigns_monotonic_non_reused_dp_ids(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session)
    tenant = Tenant(name="DP ID Tenant")
    db_session.add(tenant)
    await db_session.flush()
    await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("203.0.113.0/24"),
        actor=admin,
    )

    first = await create_service(
        db_session,
        tenant=tenant,
        actor=admin,
        name="first",
        cidr="203.0.113.10/32",
    )
    second = await create_service(
        db_session,
        tenant=tenant,
        actor=admin,
        name="second",
        cidr="203.0.113.11/32",
    )
    await db_session.delete(first.service)
    await db_session.flush()
    replacement = await create_service(
        db_session,
        tenant=tenant,
        actor=admin,
        name="replacement",
        cidr="203.0.113.12/32",
    )

    assert first.service.dp_id >= 1
    assert second.service.dp_id > first.service.dp_id
    assert replacement.service.dp_id > second.service.dp_id


async def test_dp_id_migration_backfills_existing_services_and_downgrades_cleanly(
    committed_db: async_sessionmaker[AsyncSession],
) -> None:
    del committed_db
    config = Config("alembic.ini")
    await dispose_engine()
    await asyncio.to_thread(downgrade, config, "20260710_0006")
    try:
        session_factory = get_session_factory()
        async with session_factory() as db_session:
            tenant = Tenant(name="Legacy DP ID Tenant")
            db_session.add(tenant)
            await db_session.flush()
            now = datetime.now(UTC)
            await db_session.execute(
                text(
                    "INSERT INTO protected_service "
                    "(id, tenant_id, name, cidr_or_ip, mode, enabled, apply_status, version, "
                    "created_at, updated_at) "
                    "VALUES (:id, :tenant_id, :name, :cidr_or_ip, 'allow-rule-only', false, "
                    "'pending', 1, :created_at, :updated_at)"
                ),
                [
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": tenant.id,
                        "name": "legacy-first",
                        "cidr_or_ip": "203.0.113.30/32",
                        "created_at": now,
                        "updated_at": now,
                    },
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": tenant.id,
                        "name": "legacy-second",
                        "cidr_or_ip": "203.0.113.31/32",
                        "created_at": now,
                        "updated_at": now,
                    },
                ],
            )
            await db_session.commit()

        await dispose_engine()
        await asyncio.to_thread(upgrade, config, "head")

        session_factory = get_session_factory()
        async with session_factory() as db_session:
            dp_ids = (
                (
                    await db_session.execute(
                        text("SELECT dp_id FROM protected_service ORDER BY dp_id")
                    )
                )
                .scalars()
                .all()
            )
            nullable = await db_session.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'protected_service' "
                    "AND column_name = 'dp_id'"
                )
            )
            sequence_name = await db_session.scalar(text("SELECT to_regclass('service_dp_id_seq')"))
            unique_constraint = await db_session.scalar(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'protected_service'::regclass AND contype = 'u' "
                    "AND conname = 'uq_protected_service_dp_id'"
                )
            )

        assert len(dp_ids) == 2
        assert len(set(dp_ids)) == 2
        assert all(dp_id >= 1 for dp_id in dp_ids)
        assert nullable == "NO"
        assert sequence_name == "service_dp_id_seq"
        assert unique_constraint == "uq_protected_service_dp_id"
    finally:
        await dispose_engine()
        await asyncio.to_thread(upgrade, config, "head")
