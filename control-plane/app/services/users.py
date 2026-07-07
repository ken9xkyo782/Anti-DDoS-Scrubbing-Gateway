import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import hash_password, verify_password
from app.core.sessions import RedisSessionStore
from app.db.models import Role, Tenant, User, UserStatus
from app.services.audit import record_event


async def create_user(
    db: AsyncSession,
    *,
    actor: User | None,
    sessions: RedisSessionStore,
    username: str,
    password: str,
    role: Role,
    tenant_id: uuid.UUID | None,
    ip: str | None = None,
) -> User:
    _ = sessions
    await _validate_role_tenant(db, role, tenant_id)
    await _ensure_unique_username(db, username)

    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
        tenant_id=tenant_id,
        status=UserStatus.active,
    )
    db.add(user)
    await db.flush()
    await record_event(
        db,
        actor=actor,
        action="user.create",
        target_type="user",
        target_id=str(user.id),
        outcome="success",
        ip=ip,
        metadata={"role": role.value, "tenant_id": str(tenant_id) if tenant_id else None},
    )
    return user


async def update_user(
    db: AsyncSession,
    *,
    actor: User | None,
    sessions: RedisSessionStore,
    user_id: uuid.UUID,
    username: str | None = None,
    role: Role | None = None,
    tenant_id: uuid.UUID | None = None,
    ip: str | None = None,
) -> User:
    user = await _get_existing_user(db, user_id)
    new_role = role or user.role
    new_tenant_id = tenant_id if role is not None or tenant_id is not None else user.tenant_id
    await _validate_role_tenant(db, new_role, new_tenant_id)

    changed_security_scope = False
    if username is not None and username != user.username:
        await _ensure_unique_username(db, username, exclude_user_id=user.id)
        user.username = username
    if new_role != user.role or new_tenant_id != user.tenant_id:
        if user.role == Role.admin and new_role != Role.admin:
            await _ensure_not_last_active_admin(db, user)
        user.role = new_role
        user.tenant_id = new_tenant_id
        changed_security_scope = True

    if changed_security_scope:
        await _bump_version_and_revoke(sessions, user)

    await db.flush()
    await record_event(
        db,
        actor=actor,
        action="user.update",
        target_type="user",
        target_id=str(user.id),
        outcome="success",
        ip=ip,
        metadata={
            "role": user.role.value,
            "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        },
    )
    return user


async def set_status(
    db: AsyncSession,
    *,
    actor: User | None,
    sessions: RedisSessionStore,
    user_id: uuid.UUID,
    status: UserStatus,
    ip: str | None = None,
) -> User:
    user = await _get_existing_user(db, user_id)
    if status == UserStatus.disabled:
        await _ensure_not_last_active_admin(db, user)
    if user.status != status:
        user.status = status
        await _bump_version_and_revoke(sessions, user)

    await db.flush()
    await record_event(
        db,
        actor=actor,
        action="user.status",
        target_type="user",
        target_id=str(user.id),
        outcome="success",
        ip=ip,
        metadata={"status": status.value},
    )
    return user


async def reset_password(
    db: AsyncSession,
    *,
    actor: User | None,
    sessions: RedisSessionStore,
    user_id: uuid.UUID,
    new_password: str,
    ip: str | None = None,
) -> User:
    user = await _get_existing_user(db, user_id)
    user.password_hash = hash_password(new_password)
    await _bump_version_and_revoke(sessions, user)
    await db.flush()
    await record_event(
        db,
        actor=actor,
        action="user.password.reset",
        target_type="user",
        target_id=str(user.id),
        outcome="success",
        ip=ip,
    )
    return user


async def delete_user(
    db: AsyncSession,
    *,
    actor: User | None,
    sessions: RedisSessionStore,
    user_id: uuid.UUID,
    ip: str | None = None,
) -> None:
    user = await _get_existing_user(db, user_id)
    await _ensure_not_last_active_admin(db, user)
    await sessions.revoke_all(user.id)
    await record_event(
        db,
        actor=actor,
        action="user.delete",
        target_type="user",
        target_id=str(user.id),
        outcome="success",
        ip=ip,
    )
    await db.delete(user)
    await db.flush()


async def change_own_password(
    db: AsyncSession,
    *,
    user: User,
    sessions: RedisSessionStore,
    current_session_id: str,
    current_password: str,
    new_password: str,
    ip: str | None = None,
) -> User:
    if not verify_password(current_password, user.password_hash):
        await record_event(
            db,
            actor=user,
            action="user.password.change_failed",
            target_type="user",
            target_id=str(user.id),
            outcome="denied",
            ip=ip,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user.password_hash = hash_password(new_password)
    user.session_version += 1
    for session in await sessions.list_for_user(user.id):
        if session.sid == current_session_id:
            await sessions.set_session_version(current_session_id, user.session_version)
        else:
            await sessions.revoke(session.sid)

    await db.flush()
    await record_event(
        db,
        actor=user,
        action="user.password.change",
        target_type="user",
        target_id=str(user.id),
        outcome="success",
        ip=ip,
    )
    return user


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def list_users(db: AsyncSession) -> list[User]:
    return list((await db.execute(select(User).options(selectinload(User.tenant)))).scalars().all())


async def _get_existing_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


async def _validate_role_tenant(
    db: AsyncSession,
    role: Role,
    tenant_id: uuid.UUID | None,
) -> None:
    if role == Role.admin and tenant_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid role scope",
        )
    if role == Role.tenant_user:
        if tenant_id is None or await db.get(Tenant, tenant_id) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid role scope",
            )


async def _ensure_unique_username(
    db: AsyncSession,
    username: str,
    *,
    exclude_user_id: uuid.UUID | None = None,
) -> None:
    existing = (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if existing is not None and existing.id != exclude_user_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")


async def _ensure_not_last_active_admin(db: AsyncSession, user: User) -> None:
    if user.role != Role.admin or user.status != UserStatus.active:
        return

    other_admin = (
        await db.execute(
            select(User).where(
                User.role == Role.admin,
                User.status == UserStatus.active,
                User.id != user.id,
            )
        )
    ).scalar_one_or_none()
    if other_admin is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot remove last admin")


async def _bump_version_and_revoke(sessions: RedisSessionStore, user: User) -> None:
    user.session_version += 1
    await sessions.revoke_all(user.id)
