import uuid

import pytest
from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    Principal,
    authorize_tenant_resource,
    require_admin,
    resolve_current_user,
    scope_to_tenant,
)
from app.core.sessions import RedisSessionStore
from app.db.models import Role, Tenant, TenantStatus, User, UserStatus

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def make_tenant_user(
    db_session: AsyncSession,
    *,
    username: str = "tenant-user",
    tenant_status: TenantStatus = TenantStatus.active,
    user_status: UserStatus = UserStatus.active,
) -> User:
    tenant = Tenant(name=f"{username}-tenant", status=tenant_status)
    user = User(
        username=username,
        role=Role.tenant_user,
        tenant=tenant,
        status=user_status,
        password_hash="$argon2id$hash",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def test_unknown_or_revoked_session_denies(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    user = await make_tenant_user(db_session)
    sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)
    await store.revoke(sid)

    with pytest.raises(HTTPException) as exc_info:
        await resolve_current_user(sid, db_session, store)

    assert exc_info.value.status_code == 401


async def test_disabled_user_or_inactive_tenant_denies(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    disabled = await make_tenant_user(
        db_session,
        username="disabled",
        user_status=UserStatus.disabled,
    )
    inactive_tenant_user = await make_tenant_user(
        db_session,
        username="inactive-tenant",
        tenant_status=TenantStatus.suspended,
    )

    disabled_sid = await store.create(
        user_id=disabled.id,
        session_version=disabled.session_version,
        ip=None,
    )
    inactive_sid = await store.create(
        user_id=inactive_tenant_user.id,
        session_version=inactive_tenant_user.session_version,
        ip=None,
    )

    for sid in (disabled_sid, inactive_sid):
        with pytest.raises(HTTPException) as exc_info:
            await resolve_current_user(sid, db_session, store)
        assert exc_info.value.status_code == 401


async def test_session_version_mismatch_denies(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    user = await make_tenant_user(db_session)
    sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)
    user.session_version += 1
    await db_session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await resolve_current_user(sid, db_session, store)

    assert exc_info.value.status_code == 401


async def test_require_admin_denies_tenant_user() -> None:
    principal = Principal(
        user_id=uuid.uuid4(),
        username="tenant-user",
        role=Role.tenant_user,
        tenant_id=uuid.uuid4(),
        session_id="sid",
    )

    with pytest.raises(HTTPException) as exc_info:
        require_admin(principal)

    assert exc_info.value.status_code == 403


async def test_authorize_tenant_resource_allows_admin_and_owner_only() -> None:
    tenant_id = uuid.uuid4()
    other_tenant_id = uuid.uuid4()
    admin = Principal(uuid.uuid4(), "admin", Role.admin, None, "admin-sid")
    tenant_user = Principal(uuid.uuid4(), "tenant", Role.tenant_user, tenant_id, "tenant-sid")

    assert authorize_tenant_resource(admin, None) is None
    assert authorize_tenant_resource(tenant_user, tenant_id) is None

    for scope in (other_tenant_id, None):
        with pytest.raises(HTTPException) as exc_info:
            authorize_tenant_resource(tenant_user, scope)
        assert exc_info.value.status_code == 403


async def test_scope_to_tenant_filters_tenant_users(
    db_session: AsyncSession,
) -> None:
    own = await make_tenant_user(db_session, username="own")
    other = await make_tenant_user(db_session, username="other")
    tenant_principal = Principal(
        own.id,
        own.username,
        Role.tenant_user,
        own.tenant_id,
        "tenant-sid",
    )
    admin_principal = Principal(uuid.uuid4(), "admin", Role.admin, None, "admin-sid")

    tenant_rows = (
        (await db_session.execute(scope_to_tenant(select(User), tenant_principal))).scalars().all()
    )
    admin_rows = (
        (await db_session.execute(scope_to_tenant(select(User), admin_principal))).scalars().all()
    )

    assert [user.id for user in tenant_rows] == [own.id]
    assert {user.id for user in admin_rows} >= {own.id, other.id}


async def test_resolve_current_user_uses_fresh_role_load(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    user = await make_tenant_user(db_session, username="promoted")
    sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)
    user.role = Role.admin
    user.tenant_id = None
    await db_session.flush()

    principal = await resolve_current_user(sid, db_session, store)

    assert principal.role == Role.admin
    assert principal.tenant_id is None
