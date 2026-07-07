# Testing

**Stack:** Python 3.12+ · pytest · **async** (asyncpg + SQLAlchemy 2.0 `AsyncSession`, `redis.asyncio`) · httpx `AsyncClient` + `pytest-asyncio` · ruff + mypy.
**Integration infra:** developer/CI starts `compose.test.yml` (Postgres + Redis on fixed local ports); tests connect to them.

> Greenfield conventions established here (2026-07-07) for the control-plane. Data-plane (C/XDP) and worker testing are defined by their own milestones.

## Test Types

| Type | Definition | Needs | Marker |
| --- | --- | --- | --- |
| **unit** | Pure logic, no I/O | nothing | `@pytest.mark.unit` |
| **integration** | Exercises Postgres and/or Redis, or the full HTTP path | `compose.test.yml` up | `@pytest.mark.integration` |

No mocking of the DB/Redis for integration tests — use the real services (PG-specific features `citext`/`JSONB`/`CHECK` make SQLite/fakeredis unfaithful). Each integration test runs in a rolled-back transaction or a truncated schema for isolation.

## Test Coverage Matrix

| Code layer | Location | Required test type |
| --- | --- | --- |
| Config / app factory / lifespan wiring | `app/core/config.py`, `app/main.py` | none (import smoke) |
| DB engine / session / Alembic harness | `app/db/session.py`, `migrations/` | integration |
| Models + constraints | `app/db/models.py` | integration |
| Security primitives (hash/verify, session id) | `app/core/security.py` | unit |
| Redis session store | `app/core/sessions.py` | integration |
| Audit service | `app/services/audit.py` | integration (unit for scrub helper) |
| Auth dependencies / guards | `app/core/deps.py` | integration |
| User service | `app/services/users.py` | integration |
| Auth service | `app/services/auth.py` | integration |
| API routers | `app/api/routers/*.py` | integration (API via AsyncClient) |
| Bootstrap CLI | `app/cli.py` | integration |

**Rule:** a task creating any layer above writes that layer's tests **in the same task** (highest type wins if it spans layers). `Tests: none` only where the matrix says none.

## Gate Check Commands

| Gate | Command | When |
| --- | --- | --- |
| **quick** | `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit` | unit-only tasks; fast inner loop |
| **full** | `ruff check . && ruff format --check . && mypy app/ && pytest -q` (requires `compose.test.yml` up) | any task with integration tests |
| **build** | `python -c "import app.main"` (import smoke) + `alembic upgrade head` on test DB | skeleton / wiring tasks |

Every task's **Done when** cites the expected pass count (e.g. "N tests pass") to prevent silent test deletion.

## Parallelism Assessment

| Test type | Parallel-Safe | Why |
| --- | --- | --- |
| **unit** | **Yes** | No shared state; safe to run in concurrent sub-agents |
| **integration** | **No** | All integration tests share the single `compose.test.yml` Postgres + Redis (fixed ports); concurrent sub-agents running integration suites collide on shared state |

**Consequence for `[P]`:** only tasks whose required test type is **unit** may be marked `[P]`. Any task with integration tests runs **sequentially**, even when its code has no dependencies — the shared test infra is the bottleneck.

## Conventions

- Test files: `tests/unit/test_*.py`, `tests/integration/test_*.py`.
- Async tests: `pytest-asyncio` in `auto` mode; fixtures yield `AsyncSession` / `redis.asyncio` clients bound to `compose.test.yml`.
- Shared fixtures in `tests/conftest.py`: `db_session` (transaction-rolled-back), `redis_client` (flushed per test), `client` (httpx `AsyncClient` over the ASGI app), `admin_principal` / `tenant_principal` factories.
- Secrets: tests never assert on plaintext passwords; a log-capture fixture asserts **no** credential material is emitted (Success Criteria).
