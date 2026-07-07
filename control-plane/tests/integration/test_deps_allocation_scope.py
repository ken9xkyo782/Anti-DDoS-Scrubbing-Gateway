import uuid
from ipaddress import IPv4Network

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_within_allocation
from app.db.models import Role, Tenant, User
from app.services.allocations import allocate

pytestmark = pytest.mark.integration


async def seed_allocation(db_session: AsyncSession) -> Tenant:
    actor = User(username="scope-admin", role=Role.admin, password_hash="$argon2id$hash")
    tenant = Tenant(name="Scoped Tenant")
    db_session.add_all([actor, tenant])
    await db_session.flush()
    await allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("10.70.0.0/24"),
        actor=actor,
    )
    return tenant


async def test_require_within_allocation_allows_contained_target(
    db_session: AsyncSession,
) -> None:
    tenant = await seed_allocation(db_session)

    assert (
        await require_within_allocation(
            db_session,
            tenant.id,
            IPv4Network("10.70.0.10/32"),
        )
        is None
    )


async def test_require_within_allocation_rejects_outside_target(
    db_session: AsyncSession,
) -> None:
    tenant = await seed_allocation(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await require_within_allocation(
            db_session,
            tenant.id,
            IPv4Network("10.71.0.10/32"),
        )

    assert exc_info.value.status_code == 403


async def test_require_within_allocation_fails_closed_for_unknown_or_partial(
    db_session: AsyncSession,
) -> None:
    tenant = await seed_allocation(db_session)

    for tenant_id, target in (
        (tenant.id, IPv4Network("10.70.0.0/23")),
        (uuid.uuid4(), IPv4Network("10.70.0.10/32")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await require_within_allocation(db_session, tenant_id, target)
        assert exc_info.value.status_code == 403
