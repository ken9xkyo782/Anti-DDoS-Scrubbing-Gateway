import math
from decimal import Decimal

_TWO_DECIMAL_PLACES = Decimal("0.01")
_BITS_PER_BYTE = Decimal(8)
_BYTES_PER_GIGABIT = Decimal(1_000_000_000)


def p95_nearest_rank(samples: list[int]) -> int:
    if not samples:
        return 0

    rank_index = math.ceil(0.95 * len(samples)) - 1
    return sorted(samples)[rank_index]


def bps_to_gbps(bytes_per_sec: int) -> Decimal:
    return (Decimal(bytes_per_sec) * _BITS_PER_BYTE / _BYTES_PER_GIGABIT).quantize(
        _TWO_DECIMAL_PLACES
    )
