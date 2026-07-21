from __future__ import annotations

from collections import defaultdict
from typing import TypedDict


class ResolveMetricsDict(TypedDict):
    success_count: int
    failure_count: int


_METRICS: dict[int, dict[str, int]] = defaultdict(lambda: {"success_count": 0, "failure_count": 0})


def record_resolve_result(dp_id: int, success: bool) -> None:
    if success:
        _METRICS[dp_id]["success_count"] += 1
    else:
        _METRICS[dp_id]["failure_count"] += 1


def get_resolve_metrics(dp_id: int) -> ResolveMetricsDict:
    data = _METRICS[dp_id]
    return {
        "success_count": data["success_count"],
        "failure_count": data["failure_count"],
    }
