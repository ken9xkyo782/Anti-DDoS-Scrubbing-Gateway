import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.services.feed_fetch import create_feed_client
from app.worker.applier import DoubleBufferApplier, GlobalDenyApplier
from app.worker.feed_runner import FeedRunner
from app.worker.telemetry import TelemetryAggregator
from app.worker.telemetry_reader import TelemetryReader
from app.worker.worker import Worker


def build_telemetry_aggregator(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> TelemetryAggregator | None:
    if not settings.worker_telemetry_enabled:
        return None
    return TelemetryAggregator(
        reader=TelemetryReader(
            binary=settings.worker_telemetry_binary_path,
            ifindex=settings.worker_telemetry_ifindex,
            timeout_seconds=settings.worker_telemetry_timeout_seconds,
        ),
        session_factory=session_factory,
        interval_seconds=settings.worker_telemetry_interval_seconds,
        retention_seconds=settings.worker_telemetry_retention_seconds,
        node_clean_capacity_gbps=settings.node_clean_capacity_gbps,
        top_talkers_window_seconds=settings.worker_telemetry_top_talkers_window_seconds,
        top_talkers_limit=settings.worker_telemetry_top_talkers_limit,
    )


async def _run_worker() -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    client = create_feed_client(settings)
    runner = FeedRunner(
        client=client,
        settings=settings,
        global_applier=GlobalDenyApplier(
            apply_bin=settings.worker_apply_binary_path,
            timeout_seconds=settings.worker_apply_timeout_seconds,
        ),
    )
    await Worker(
        settings=settings,
        session_factory=session_factory,
        applier=DoubleBufferApplier(
            session_factory=session_factory,
            apply_bin=settings.worker_apply_binary_path,
            timeout_seconds=settings.worker_apply_timeout_seconds,
        ),
        feed_runner=runner,
        telemetry=build_telemetry_aggregator(settings, session_factory),
    ).run()


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run_worker())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
