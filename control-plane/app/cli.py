import argparse
import asyncio

from app.db.session import dispose_engine, get_session_factory
from app.services.auth import bootstrap_admin


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bootstrap-admin")
    subparsers.add_parser("non-host-services-report")
    args = parser.parse_args()

    if args.command == "bootstrap-admin":
        asyncio.run(_bootstrap_admin())
        return 0
    elif args.command == "non-host-services-report":
        asyncio.run(_non_host_services_report())
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


async def _non_host_services_report() -> None:
    from app.services.services import list_non_host_services
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            records = await list_non_host_services(session)
            if not records:
                print("No non-host services found.")
                return
            header = (
                f"{'Service ID':<36} | {'DP ID':<6} | "
                f"{'Tenant ID':<36} | {'Name':<20} | {'CIDR':<18}"
            )
            print(header)
            print("-" * 128)
            for r in records:
                row = (
                    f"{str(r.service.id):<36} | {r.service.dp_id:<6} | "
                    f"{str(r.service.tenant_id):<36} | {r.service.name:<20} | "
                    f"{r.service.cidr_or_ip:<18}"
                )
                print(row)
        except Exception:
            raise
        finally:
            await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(main())
