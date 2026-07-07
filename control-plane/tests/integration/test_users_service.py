import pytest
from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.core.sessions import RedisSessionStore
from app.db.models import AuditEvent, Role, Tenant, User, UserStatus
from app.services import users

pytestmark = pytest.mark.integration


def make_store(redis_client: Redis) -> RedisSessionStore:
    return RedisSessionStore(redis_client, idle_seconds=30, absolute_seconds=60)


async def create_admin(db_session: AsyncSession, username: str = "admin") -> User:
    admin = User(username=username, role=Role.admin, password_hash=hash_password("admin-pass"))
    db_session.add(admin)
    await db_session.flush()
    return admin


async def create_tenant(db_session: AsyncSession, name: str = "Tenant") -> Tenant:
    tenant = Tenant(name=name)
    db_session.add(tenant)
    await db_session.flush()
    return tenant


async def test_create_user_persists_hashed_password_and_audit(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session)

    created = await users.create_user(
        db_session,
        actor=admin,
        sessions=make_store(redis_client),
        username="new-user",
        password="user-passphrase",
        role=Role.tenant_user,
        tenant_id=tenant.id,
    )

    assert created.id is not None
    assert created.status == UserStatus.active
    assert created.password_hash != "user-passphrase"
    assert verify_password("user-passphrase", created.password_hash)
    event = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "user.create"))
    ).scalar_one()
    assert event.actor_user_id == admin.id
    assert event.target_id == str(created.id)


async def test_create_user_rejects_invalid_role_tenant_pair(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session)

    invalid_pairs = [
        {"role": Role.admin, "tenant_id": tenant.id},
        {"role": Role.tenant_user, "tenant_id": None},
    ]
    for pair in invalid_pairs:
        with pytest.raises(HTTPException) as exc_info:
            await users.create_user(
                db_session,
                actor=admin,
                sessions=make_store(redis_client),
                username=f"invalid-{pair['role']}",
                password="passphrase",
                role=pair["role"],
                tenant_id=pair["tenant_id"],
            )
        assert exc_info.value.status_code == 422


async def test_create_user_rejects_duplicate_username(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    admin = await create_admin(db_session)

    await users.create_user(
        db_session,
        actor=admin,
        sessions=make_store(redis_client),
        username="CaseUser",
        password="passphrase",
        role=Role.admin,
        tenant_id=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        await users.create_user(
            db_session,
            actor=admin,
            sessions=make_store(redis_client),
            username="caseuser",
            password="passphrase",
            role=Role.admin,
            tenant_id=None,
        )

    assert exc_info.value.status_code == 409


async def test_update_user_role_and_tenant_revokes_sessions_and_audits(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session)
    user = await users.create_user(
        db_session,
        actor=admin,
        sessions=store,
        username="promote-me",
        password="passphrase",
        role=Role.tenant_user,
        tenant_id=tenant.id,
    )
    sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)

    updated = await users.update_user(
        db_session,
        actor=admin,
        sessions=store,
        user_id=user.id,
        role=Role.admin,
        tenant_id=None,
    )

    assert updated.role == Role.admin
    assert updated.tenant_id is None
    assert updated.session_version == 2
    assert await store.get(sid) is None
    event = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "user.update"))
    ).scalar_one()
    assert event.target_id == str(user.id)


async def test_set_status_disabled_bumps_version_revokes_and_audits(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, username="admin-a")
    other_admin = await create_admin(db_session, username="admin-b")
    sid = await store.create(
        user_id=other_admin.id,
        session_version=other_admin.session_version,
        ip=None,
    )

    disabled = await users.set_status(
        db_session,
        actor=admin,
        sessions=store,
        user_id=other_admin.id,
        status=UserStatus.disabled,
    )

    assert disabled.status == UserStatus.disabled
    assert disabled.session_version == 2
    assert await store.get(sid) is None
    event = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "user.status"))
    ).scalar_one()
    assert event.target_id == str(other_admin.id)


async def test_reset_password_updates_hash_bumps_version_and_revokes(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session)
    target = await create_admin(db_session, username="reset-target")
    sid = await store.create(user_id=target.id, session_version=target.session_version, ip=None)

    changed = await users.reset_password(
        db_session,
        actor=admin,
        sessions=store,
        user_id=target.id,
        new_password="new-passphrase",
    )

    assert verify_password("new-passphrase", changed.password_hash)
    assert changed.session_version == 2
    assert await store.get(sid) is None
    event = (
        await db_session.execute(
            select(AuditEvent).where(AuditEvent.action == "user.password.reset")
        )
    ).scalar_one()
    assert event.target_id == str(target.id)


async def test_delete_user_removes_account_revokes_and_audits(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    admin = await create_admin(db_session, username="admin-a")
    target = await create_admin(db_session, username="delete-target")
    sid = await store.create(user_id=target.id, session_version=target.session_version, ip=None)

    await users.delete_user(db_session, actor=admin, sessions=store, user_id=target.id)

    assert await db_session.get(User, target.id) is None
    assert await store.get(sid) is None
    event = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.action == "user.delete"))
    ).scalar_one()
    assert event.target_id == str(target.id)


async def test_change_own_password_keeps_current_session_and_revokes_others(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    store = make_store(redis_client)
    user = await create_admin(db_session)
    current_sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)
    other_sid = await store.create(user_id=user.id, session_version=user.session_version, ip=None)

    changed = await users.change_own_password(
        db_session,
        user=user,
        sessions=store,
        current_session_id=current_sid,
        current_password="admin-pass",
        new_password="new-passphrase",
    )

    current = await store.get(current_sid)
    assert changed.session_version == 2
    assert current is not None
    assert current.session_version == changed.session_version
    assert await store.get(other_sid) is None
    event = (
        await db_session.execute(
            select(AuditEvent).where(AuditEvent.action == "user.password.change")
        )
    ).scalar_one()
    assert event.target_id == str(user.id)


async def test_change_own_password_wrong_current_password_rejected_and_audited(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    user = await create_admin(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await users.change_own_password(
            db_session,
            user=user,
            sessions=make_store(redis_client),
            current_session_id="sid",
            current_password="wrong",
            new_password="new-passphrase",
        )

    assert exc_info.value.status_code == 401
    event = (
        await db_session.execute(
            select(AuditEvent).where(AuditEvent.action == "user.password.change_failed")
        )
    ).scalar_one()
    assert event.outcome == "denied"


async def test_last_active_admin_cannot_be_disabled_or_deleted(
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    admin = await create_admin(db_session)
    store = make_store(redis_client)

    for operation in (
        users.set_status(
            db_session,
            actor=admin,
            sessions=store,
            user_id=admin.id,
            status=UserStatus.disabled,
        ),
        users.delete_user(db_session, actor=admin, sessions=store, user_id=admin.id),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await operation
        assert exc_info.value.status_code == 409
