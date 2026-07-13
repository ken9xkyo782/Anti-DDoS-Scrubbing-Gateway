import asyncio
import logging

from app.core.config import get_settings
from app.services.feed_fetch import create_feed_client
from app.services.feed_reconcile import GlobalDenySnapshot
from app.worker.applier import PlaceholderApplier
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
        applier=PlaceholderApplier(),
        feed_runner=runner,
    ).run()


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run_worker())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
