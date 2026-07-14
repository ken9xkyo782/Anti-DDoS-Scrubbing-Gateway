import csv
import uuid
from datetime import UTC, datetime
from io import StringIO
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.billing import BillingUsageListResponse, BillingUsageResponse
from app.core.deps import Principal, get_current_user, load_service_for_principal, require_admin
from app.db.models import BillingStatus, BillingUsage, Role, Tenant
from app.db.session import get_db
from app.services.billing_period import month_period

router = APIRouter(prefix="/billing", tags=["billing"])

_PERIOD_PATTERN = r"^[1-9]\d{3}-(0[1-9]|1[0-2])$"


@router.get("/usage", response_model=BillingUsageListResponse)
async def list_usage(
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    service_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    period: Annotated[str | None, Query(pattern=_PERIOD_PATTERN)] = None,
    status_filter: Annotated[BillingStatus | None, Query(alias="status")] = None,
) -> BillingUsageListResponse:
    if service_id is not None:
        await load_service_for_principal(db, service_id, principal)

    statement = select(BillingUsage)
    if principal.role is Role.admin:
        if tenant_id is not None:
            statement = statement.where(BillingUsage.tenant_id == tenant_id)
    elif principal.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    else:
        statement = statement.where(BillingUsage.tenant_id == principal.tenant_id)

    if service_id is not None:
        statement = statement.where(BillingUsage.service_id == service_id)
    if period is not None:
        period_start, period_end = _period_bounds(period)
        statement = statement.where(
            BillingUsage.period_start == period_start,
            BillingUsage.period_end == period_end,
        )
    if status_filter is not None:
        statement = statement.where(BillingUsage.status == status_filter)

    usages = list(
        (
            await db.scalars(
                statement.order_by(
                    BillingUsage.period_start.desc(), BillingUsage.service_name, BillingUsage.id
                )
            )
        ).all()
    )
    return BillingUsageListResponse(
        usage=[_usage_response(usage) for usage in usages],
        has_data=bool(usages),
    )


@router.get("/usage/history", response_model=BillingUsageListResponse)
async def usage_history(
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    service_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 12,
) -> BillingUsageListResponse:
    if service_id is not None:
        await load_service_for_principal(db, service_id, principal)

    statement = select(BillingUsage).where(BillingUsage.status == BillingStatus.final)
    if principal.role is not Role.admin:
        if principal.tenant_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        statement = statement.where(BillingUsage.tenant_id == principal.tenant_id)
    if service_id is not None:
        statement = statement.where(BillingUsage.service_id == service_id)

    usages = list(
        (
            await db.scalars(
                statement.order_by(
                    BillingUsage.period_start.desc(), BillingUsage.service_name, BillingUsage.id
                ).limit(limit)
            )
        ).all()
    )
    return BillingUsageListResponse(
        usage=[_usage_response(usage) for usage in usages],
        has_data=bool(usages),
    )


@router.get("/usage/export", response_model=None)
async def export_usage(
    period: Annotated[str, Query(pattern=_PERIOD_PATTERN)],
    export_format: Annotated[Literal["csv", "json"], Query(alias="format")],
    principal: Annotated[Principal, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BillingUsageListResponse | StreamingResponse:
    require_admin(principal)
    period_start, period_end = _period_bounds(period)
    statement = (
        select(BillingUsage, Tenant.name)
        .outerjoin(Tenant, Tenant.id == BillingUsage.tenant_id)
        .where(
            BillingUsage.period_start == period_start,
            BillingUsage.period_end == period_end,
            BillingUsage.status == BillingStatus.final,
        )
        .order_by(BillingUsage.service_name, BillingUsage.id)
    )
    rows: list[tuple[BillingUsage, str | None]] = [
        (usage, tenant_name) for usage, tenant_name in (await db.execute(statement)).all()
    ]

    if export_format == "csv":
        return _csv_response(rows, period)

    usages = [usage for usage, _tenant_name in rows]
    return BillingUsageListResponse(
        usage=[_usage_response(usage) for usage in usages],
        has_data=bool(usages),
    )


def _period_bounds(period: str) -> tuple[datetime, datetime]:
    year, month = (int(value) for value in period.split("-", maxsplit=1))
    return month_period(datetime(year, month, 1, tzinfo=UTC))


def _usage_response(usage: BillingUsage) -> BillingUsageResponse:
    return BillingUsageResponse(
        service_id=usage.service_id,
        service_name=usage.service_name,
        tenant_id=usage.tenant_id,
        period_start=usage.period_start,
        period_end=usage.period_end,
        billing_metric=usage.billing_metric,
        committed_clean_gbps=usage.committed_clean_gbps,
        p95_clean_gbps=usage.p95_clean_gbps,
        billed_gbps=usage.billed_gbps,
        overage_gbps=usage.overage_gbps,
        overage_policy=usage.overage_policy,
        sample_count=usage.sample_count,
        status=usage.status,
        provisional=usage.status is BillingStatus.open,
    )


def _csv_response(
    rows: list[tuple[BillingUsage, str | None]],
    period: str,
) -> StreamingResponse:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "service",
            "tenant",
            "period",
            "committed",
            "p95",
            "billed",
            "overage",
            "overage_policy",
            "sample_count",
        ]
    )
    for usage, tenant_name in rows:
        writer.writerow(
            [
                usage.service_name,
                tenant_name or str(usage.tenant_id or ""),
                period,
                str(usage.committed_clean_gbps),
                str(usage.p95_clean_gbps),
                str(usage.billed_gbps),
                str(usage.overage_gbps),
                usage.overage_policy.value,
                usage.sample_count,
            ]
        )
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="billing-usage-{period}.csv"'},
    )
