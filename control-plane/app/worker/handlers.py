from collections.abc import Awaitable, Callable

from app.db.models import AgentJob, JobType
from app.db.session import session_scope
from app.worker.applier import Applier, load_service_config

Handler = Callable[[AgentJob, Applier], Awaitable[None]]


class NoHandlerError(Exception):
    pass


async def handle_service_update(
    job: AgentJob,
    applier: Applier,
) -> None:
    async with session_scope() as db:
        config = await load_service_config(db, job.target_id)
    if config is None:
        raise RuntimeError("service missing")
    await applier.apply(config)


HANDLERS: dict[JobType, Handler] = {JobType.service_update: handle_service_update}
