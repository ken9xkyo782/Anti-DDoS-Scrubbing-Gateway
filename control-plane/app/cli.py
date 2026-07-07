import argparse
import asyncio

from app.db.session import dispose_engine, get_session_factory
from app.services.auth import bootstrap_admin


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bootstrap-admin")
    args = parser.parse_args()

    if args.command == "bootstrap-admin":
        asyncio.run(_bootstrap_admin())
        return 0
    return 1


async def _bootstrap_admin() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            user = await bootstrap_admin(session)
            await session.commit()
            print(f"bootstrap admin ready: {user.username}")
        except Exception:
            await session.rollback()
            raise
        finally:
            await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(main())
