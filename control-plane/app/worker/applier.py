import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AllowRule,
    BlacklistEntry,
    ProtectedService,
    ServiceMode,
    ServicePlan,
    WhitelistEntry,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceConfig:
    service_id: uuid.UUID
    version: int
    name: str
    cidr_or_ip: str
    mode: ServiceMode
    enabled: bool
    vip_pps: int | None
    vip_bps: int | None
    plan: ServicePlan | None
    rules: tuple[AllowRule, ...]
    whitelist: tuple[WhitelistEntry, ...]
    blacklist: tuple[BlacklistEntry, ...]


class Applier(Protocol):
    async def apply(self, config: ServiceConfig) -> None: ...


class PlaceholderApplier:
    async def apply(self, config: ServiceConfig) -> None:
        logger.info(
            "placeholder apply acknowledged",
            extra={
                "service_id": str(config.service_id),
                "service_name": config.name,
                "version": config.version,
                "rule_count": len(config.rules),
                "whitelist_count": len(config.whitelist),
                "blacklist_count": len(config.blacklist),
            },
        )


async def load_service_config(
    db: AsyncSession,
    service_id: uuid.UUID,
) -> ServiceConfig | None:
    service = (
        (
            await db.execute(
                select(ProtectedService)
                .options(
                    selectinload(ProtectedService.plan),
                    selectinload(ProtectedService.rules),
                    selectinload(ProtectedService.whitelist_entries),
                    selectinload(ProtectedService.blacklist_entries),
                )
                .where(ProtectedService.id == service_id)
            )
        )
        .scalars()
        .one_or_none()
    )
    if service is None:
        return None

    return ServiceConfig(
        service_id=service.id,
        version=service.version,
        name=service.name,
        cidr_or_ip=service.cidr_or_ip,
        mode=service.mode,
        enabled=service.enabled,
        vip_pps=service.vip_pps,
        vip_bps=service.vip_bps,
        plan=service.plan,
        rules=tuple(service.rules),
        whitelist=tuple(service.whitelist_entries),
        blacklist=tuple(service.blacklist_entries),
    )
