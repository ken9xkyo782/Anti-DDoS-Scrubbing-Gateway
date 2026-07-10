from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentJob, JobType
from app.worker.applier import Applier, load_service_config

Handler = Callable[[AsyncSession, AgentJob, Applier], Awaitable[None]]


class NoHandlerError(Exception):
    pass


async def handle_service_update(
    db: AsyncSession,
    job: AgentJob,
    applier: Applier,
) -> None:
    config = await load_service_config(db, job.target_id)
    if config is None:
        raise RuntimeError("service missing")
    await applier.apply(config)


HANDLERS: dict[JobType, Handler] = {JobType.service_update: handle_service_update}
