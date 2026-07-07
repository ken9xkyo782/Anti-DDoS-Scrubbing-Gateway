from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def test_async_session_executes_select(db_session: AsyncSession) -> None:
    result = await db_session.execute(text("SELECT 1"))

    assert result.scalar_one() == 1
