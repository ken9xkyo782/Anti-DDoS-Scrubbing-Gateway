import asyncio
import logging

from app.core.config import get_settings
from app.worker.applier import PlaceholderApplier
from app.worker.worker import Worker


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(Worker(settings=get_settings(), applier=PlaceholderApplier()).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
