from decimal import Decimal

from app.db.models import ServicePlan


def committed_clean_bps(plan: ServicePlan | None) -> int:
    if plan is None:
        return 0
    return int(plan.committed_clean_gbps * Decimal("1000000000"))


def committed_honored(bps: int, plan: ServicePlan | None) -> bool | None:
    if plan is None:
        return None
    return bps >= committed_clean_bps(plan)
