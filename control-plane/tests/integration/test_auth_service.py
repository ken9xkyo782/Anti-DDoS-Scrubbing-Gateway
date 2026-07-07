import pytest
from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.core.sessions import RedisSessionStore
from app.db.models import AuditEvent, Role, Tenant, TenantStatus, User, UserStatus
from app.services import auth

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def create_user(
    db_session: AsyncSession,
    *,
    username: str = "login-user",
    password: str = "login-pass",
    role: Role = Role.admin,
    status: UserStatus = UserStatus.active,
    tenant_status: TenantStatus = TenantStatus.active,
) -> User:
    tenant = None
    if role == Role.tenant_user:
        tenant = Tenant(name=f"{username}-tenant", status=tenant_status)
    user = User(
        username=username,
        role=role,
        tenant=tenant,
        status=status,
        password_hash=hash_password(password),
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def test_valid_login_creates_session_updates_last_login_and_audits(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    user = await create_user(db_session)

    result = await auth.login(
        db_session,
        sessions=make_store(redis_client),
        username=user.username,
        password="login-pass",
        ip="127.0.0.1",
    )

    assert result.user.id == user.id
    assert await make_store(redis_client).get(result.session_id) is not None
    assert user.last_login_at is not None
    event = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "auth.login"))
    ).scalar_one()
    assert event.actor_user_id == user.id
    assert event.outcome == "success"


async def test_invalid_credentials_fail_generically_and_audit(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    user = await create_user(db_session)

    for username, password in ((user.username, "wrong"), ("missing", "anything")):
        with pytest.raises(HTTPException) as exc_info:
            await auth.login(
                db_session,
                sessions=make_store(redis_client),
                username=username,
                password=password,
            )
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid credentials"

    events = (
        (
            await db_session.execute(
                select(AuditEvent).where(AuditEvent.action == "auth.login.failed")
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 2
    assert all(event.outcome == "denied" for event in events)
    assert all("password" not in event.metadata_ for event in events)


async def test_disabled_user_and_inactive_tenant_login_refused(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    disabled = await create_user(
        db_session,
        username="disabled-login",
        status=UserStatus.disabled,
    )
    inactive_tenant_user = await create_user(
        db_session,
        username="inactive-login",
        role=Role.tenant_user,
        tenant_status=TenantStatus.suspended,
    )

    for username in (disabled.username, inactive_tenant_user.username):
        with pytest.raises(HTTPException) as exc_info:
            await auth.login(
                db_session,
                sessions=make_store(redis_client),
                username=username,
                password="login-pass",
            )
        assert exc_info.value.status_code == 401


async def test_logout_revokes_session_and_audits(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    user = await create_user(db_session)
    result = await auth.login(
        db_session,
        sessions=store,
        username=user.username,
        password="login-pass",
    )

    await auth.logout(db_session, actor=user, sessions=store, session_id=result.session_id)

    assert await store.get(result.session_id) is None
    event = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "auth.logout"))
    ).scalar_one()
    assert event.actor_user_id == user.id


async def test_bootstrap_admin_creates_one_admin_from_env_and_is_idempotent(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTROL_PLANE_BOOTSTRAP_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_PLANE_BOOTSTRAP_ADMIN_PASSWORD", "root-passphrase")
    get_settings.cache_clear()

    created = await auth.bootstrap_admin(db_session)
    again = await auth.bootstrap_admin(db_session)

    admins = (await db_session.execute(select(User).where(User.role == Role.admin))).scalars().all()
    assert created.id == again.id
    assert len(admins) == 1
    assert admins[0].username == "root"
    assert verify_password("root-passphrase", admins[0].password_hash)
    events = (
        (await db_session.execute(select(AuditEvent).where(AuditEvent.action == "auth.bootstrap")))
        .scalars()
        .all()
    )
    assert len(events) == 1


async def test_bootstrap_admin_does_not_overwrite_existing_admin(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = await create_user(db_session, username="existing-root", password="old-pass")
    monkeypatch.setenv("CONTROL_PLANE_BOOTSTRAP_ADMIN_USERNAME", "new-root")
    monkeypatch.setenv("CONTROL_PLANE_BOOTSTRAP_ADMIN_PASSWORD", "new-pass")
    get_settings.cache_clear()

    returned = await auth.bootstrap_admin(db_session)

    assert returned.id == existing.id
    assert returned.username == "existing-root"
    assert verify_password("old-pass", returned.password_hash)
    assert not verify_password("new-pass", returned.password_hash)
