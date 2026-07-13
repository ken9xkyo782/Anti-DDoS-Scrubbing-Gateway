import asyncio
import logging

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.services.feed_fetch import create_feed_client
from app.services.feed_reconcile import GlobalDenySnapshot
from app.worker.applier import DoubleBufferApplier
from app.worker.feed_runner import FeedRunner, GlobalDenyApplyResult
from app.worker.worker import Worker


class _UnavailableGlobalDenyApplier:
    async def apply_global(self, snapshot: GlobalDenySnapshot) -> GlobalDenyApplyResult:
        del snapshot
        raise RuntimeError("global deny applier is not configured")


async def _run_worker() -> None:
    settings = get_settings()
    client = create_feed_client(settings)
    runner = FeedRunner(
        client=client,
        settings=settings,
        global_applier=_UnavailableGlobalDenyApplier(),
    )
    await Worker(
        settings=settings,
        applier=DoubleBufferApplier(
            session_factory=get_session_factory(),
            apply_bin=settings.worker_apply_binary_path,
            timeout_seconds=settings.worker_apply_timeout_seconds,
        ),
        feed_runner=runner,
    ).run()


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run_worker())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
