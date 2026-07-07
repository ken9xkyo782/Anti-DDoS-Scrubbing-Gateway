from collections.abc import AsyncGenerator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    from app.db.session import dispose_engine, get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as session:
        transaction = await session.begin()
        try:
            yield session
        finally:
            if transaction.is_active:
                await transaction.rollback()
            await session.close()
            await dispose_engine()
