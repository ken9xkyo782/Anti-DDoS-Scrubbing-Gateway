import os
import subprocess
import sys
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import verify_password
from app.db.models import AuditEvent, Role, Tenant, User
from app.db.session import dispose_engine, get_session_factory
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
async def clean_database(migrated_db: None) -> AsyncGenerator[None, None]:
    _ = migrated_db
    await clear_database()
    try:
        yield
    finally:
        await clear_database()
        await dispose_engine()


async def clear_database() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(delete(AuditEvent))
        await session.execute(delete(User))
        await session.execute(delete(Tenant))
        await session.commit()


def run_bootstrap_cli(username: str, password: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "CONTROL_PLANE_BOOTSTRAP_ADMIN_USERNAME": username,
        "CONTROL_PLANE_BOOTSTRAP_ADMIN_PASSWORD": password,
    }
    return subprocess.run(
        [sys.executable, "-m", "app.cli", "bootstrap-admin"],
        cwd=os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


async def test_bootstrap_cli_creates_first_admin_from_env(
    clean_database: None,
) -> None:
    _ = clean_database
    result = run_bootstrap_cli("root-cli", "root-passphrase")

    assert result.returncode == 0
    async with get_session_factory()() as session:
        admin = (await session.execute(select(User).where(User.role == Role.admin))).scalar_one()
        assert admin.username == "root-cli"
        assert verify_password("root-passphrase", admin.password_hash)


async def test_bootstrap_cli_is_idempotent(clean_database: None) -> None:
    _ = clean_database
    assert run_bootstrap_cli("root-cli", "first-passphrase").returncode == 0
    assert run_bootstrap_cli("other-root", "second-passphrase").returncode == 0

    async with get_session_factory()() as session:
        admins = (
            (await session.execute(select(User).where(User.role == Role.admin))).scalars().all()
        )
        assert len(admins) == 1
        assert admins[0].username == "root-cli"
        assert verify_password("first-passphrase", admins[0].password_hash)


async def test_e2e_bootstrap_admin_login_create_tenant_user_login(
    clean_database: None,
) -> None:
    _ = clean_database
    assert run_bootstrap_cli("root-e2e", "root-passphrase").returncode == 0
    async with get_session_factory()() as session:
        tenant = Tenant(name="E2E Tenant")
        session.add(tenant)
        await session.commit()
        tenant_id = tenant.id

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        admin_login = await client.post(
            "/auth/login",
            json={"username": "root-e2e", "password": "root-passphrase"},
        )
        assert admin_login.status_code == 200

        created = await client.post(
            "/users",
            json={
                "username": "tenant-e2e",
                "password": "tenant-passphrase",
                "role": "tenant_user",
                "tenant_id": str(tenant_id),
            },
        )
        assert created.status_code == 201

        await client.post("/auth/logout")
        tenant_login = await client.post(
            "/auth/login",
            json={"username": "tenant-e2e", "password": "tenant-passphrase"},
        )
        assert tenant_login.status_code == 200
        assert tenant_login.json()["role"] == "tenant_user"
