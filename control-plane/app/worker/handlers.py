import ipaddress
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.db.models import AgentJob, JobType
from app.db.session import session_scope
from app.worker.applier import Applier, load_service_config
from app.worker.feed_runner import FeedRunner

Handler = Callable[[AgentJob, Applier], Awaitable[None]]


class NoHandlerError(Exception):
    pass


class NextHopLane(Protocol):
    def request_resolve(self, dp_id: int, ip: str) -> None: ...
    def request_evict(self, dp_id: int) -> None: ...


_feed_runner: FeedRunner | None = None
_nexthop_resolver: NextHopLane | None = None


def configure_feed_runner(runner: FeedRunner | None) -> None:
    """Install worker-lifetime feed dependencies until the coordinator owns wiring."""
    global _feed_runner
    _feed_runner = runner


def configure_nexthop_resolver(resolver: NextHopLane | None) -> None:
    """Install worker-lifetime next-hop resolver dependency."""
    global _nexthop_resolver
    _nexthop_resolver = resolver


async def handle_service_update(
    job: AgentJob,
    applier: Applier,
) -> None:
    async with session_scope() as db:
        config = await load_service_config(db, job.target_id)
    if config is None:
        raise RuntimeError("service missing")
    await applier.apply(config)
    if _nexthop_resolver is not None:
        if config.enabled:
            ip_net = ipaddress.ip_network(config.cidr_or_ip, strict=False)
            ip_str = str(ip_net.network_address)
            _nexthop_resolver.request_resolve(config.dp_id, ip_str)
        else:
            _nexthop_resolver.request_evict(config.dp_id)


async def handle_feed_sync(job: AgentJob, applier: Applier) -> None:
    del applier
    runner = _require_feed_runner()
    await runner.handle_feed_sync(job)


async def handle_global_deny_apply(job: AgentJob, applier: Applier) -> None:
    del applier
    runner = _require_feed_runner()
    await runner.handle_global_deny_apply(job)


def _require_feed_runner() -> FeedRunner:
    if _feed_runner is None:
        raise RuntimeError("feed runner dependencies are not configured")
    return _feed_runner


HANDLERS: dict[JobType, Handler] = {
    JobType.service_update: handle_service_update,
    JobType.feed_sync: handle_feed_sync,
    JobType.global_deny_apply: handle_global_deny_apply,
}
