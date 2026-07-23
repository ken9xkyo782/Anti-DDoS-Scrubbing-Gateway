from ipaddress import IPv4Network

import pytest
from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AgentJob,
    AuditEvent,
    BlacklistEntry,
    BlacklistScope,
    BlacklistSource,
    FeedBlacklistAssertion,
    GlobalDenyState,
    JobType,
    Role,
    Tenant,
    ThreatFeedSource,
    User,
    WhitelistEntry,
    utc_now,
)
from app.db.session import run_post_commit_callbacks
from app.services import allocations as allocation_service
from app.services import feed_reconcile
from app.services import lists as list_service
from app.services import services as service_service
from app.services.apply import APPLY_QUEUE_KEY

pytestmark = pytest.mark.integration


async def create_admin(db_session: AsyncSession, username: str = "list-admin") -> User:
    user = User(username=username, role=Role.admin, password_hash="$argon2id$hash")
    db_session.add(user)
    await db_session.flush()
    return user


async def create_tenant(db_session: AsyncSession, name: str) -> Tenant:
    tenant = Tenant(name=name)
    db_session.add(tenant)
    await db_session.flush()
    return tenant


async def create_tenant_user(
    db_session: AsyncSession,
    *,
    username: str,
    tenant: Tenant,
) -> User:
    user = User(
        username=username,
        role=Role.tenant_user,
        tenant=tenant,
        password_hash="$argon2id$hash",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def create_service(
    db_session: AsyncSession,
    *,
    tenant: Tenant,
    actor: User,
) -> service_service.ServiceRecord:
    await allocation_service.allocate(
        db_session,
        tenant_id=tenant.id,
        cidr=IPv4Network("203.0.113.0/24"),
        actor=actor,
    )
    return await service_service.create_service(
        db_session,
        tenant_id=tenant.id,
        name="list-service",
        cidr_or_ip=IPv4Network("203.0.113.10/32"),
        actor=actor,
    )


async def seed_feed_entry(
    db_session: AsyncSession,
    *,
    source_cidr: str,
    source_count: int = 1,
) -> tuple[BlacklistEntry, list[ThreatFeedSource]]:
    entry = BlacklistEntry(
        scope=BlacklistScope.global_,
        source=BlacklistSource.feed,
        source_cidr=source_cidr,
    )
    sources = [
        ThreatFeedSource(
            name=f"feed-list-{source_cidr}-{index}",
            url=f"https://feeds.example.test/{source_cidr}/{index}",
            sync_interval_seconds=300,
        )
        for index in range(source_count)
    ]
    db_session.add_all([entry, *sources])
    await db_session.flush()
    now = utc_now()
    db_session.add_all(
        [
            FeedBlacklistAssertion(
                feed_source_id=source.id,
                blacklist_entry_id=entry.id,
                first_seen_at=now,
                last_seen_at=now,
            )
            for source in sources
        ]
    )
    await db_session.flush()
    return entry, sources


async def materialize_state(db_session: AsyncSession) -> GlobalDenyState:
    await feed_reconcile.materialize_global_union(db_session)
    state = await db_session.get(GlobalDenyState, 1)
    assert state is not None
    return state


async def test_add_whitelist_accepts_external_ipv4_bumps_and_audits(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session)
    tenant = await create_tenant(db_session, "Whitelist Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    entry = await list_service.add_whitelist(
        db_session,
        service_id=service.service.id,
        source_cidr="198.51.100.7/32",
        actor=admin,
    )

    assert str(entry.source_cidr) == "198.51.100.7/32"
    assert service.service.version == 2
    audit = (
        await db_session.execute(
            select(AuditEvent).where(AuditEvent.action == "list.whitelist.add")
        )
    ).scalar_one()
    assert audit.target_id == str(entry.id)


@pytest.mark.parametrize("source_cidr", ["2001:db8::/48", "198.51.100.7/24"])
async def test_add_whitelist_rejects_ipv6_and_host_bits(
    db_session: AsyncSession,
    source_cidr: str,
) -> None:
    admin = await create_admin(db_session, f"whitelist-invalid-{source_cidr}")
    tenant = await create_tenant(db_session, f"Whitelist Invalid {source_cidr}")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    with pytest.raises(HTTPException) as exc_info:
        await list_service.add_whitelist(
            db_session,
            service_id=service.service.id,
            source_cidr=source_cidr,
            actor=admin,
        )

    assert exc_info.value.status_code == 422


async def test_add_whitelist_does_not_require_source_inside_allocation(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "whitelist-external-admin")
    tenant = await create_tenant(db_session, "Whitelist External Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    entry = await list_service.add_whitelist(
        db_session,
        service_id=service.service.id,
        source_cidr="45.0.0.0/8",
        actor=admin,
    )

    assert str(entry.source_cidr) == "45.0.0.0/8"





async def test_add_global_blacklist_has_manual_source_and_no_version_bump(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "global-blacklist-admin")
    tenant = await create_tenant(db_session, "Global Blacklist Tenant")
    service = await create_service(db_session, tenant=tenant, actor=admin)

    entry = await list_service.add_blacklist(
        db_session,
        source_cidr="45.0.0.0/8",
        actor=admin,
    )

    assert entry.scope == BlacklistScope.global_
    assert entry.source == BlacklistSource.manual
    assert service.service.version == 1


async def test_add_global_manual_promotes_feed_entry_and_preserves_assertions(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "global-promote-admin")
    feed_entry, sources = await seed_feed_entry(
        db_session,
        source_cidr="185.1.0.0/16",
        source_count=2,
    )
    state = await materialize_state(db_session)
    desired_revision = state.desired_revision

    entry = await list_service.add_blacklist(
        db_session,
        source_cidr="185.1.0.0/16",
        actor=admin,
    )

    assertions = list(
        (
            await db_session.scalars(
                select(FeedBlacklistAssertion).where(
                    FeedBlacklistAssertion.blacklist_entry_id == feed_entry.id
                )
            )
        ).all()
    )
    state = await db_session.get(GlobalDenyState, 1)
    assert entry.id == feed_entry.id
    assert entry.source == BlacklistSource.manual
    assert entry.created_by == admin.id
    assert {assertion.feed_source_id for assertion in assertions} == {
        source.id for source in sources
    }
    assert state is not None
    assert state.desired_revision == desired_revision
    assert (
        await db_session.scalar(
            select(func.count(AgentJob.id)).where(AgentJob.job_type == JobType.global_deny_apply)
        )
    ) == 0


async def test_remove_global_manual_demotes_to_feed_when_assertions_remain(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "global-demote-admin")
    entry, sources = await seed_feed_entry(
        db_session,
        source_cidr="185.2.0.0/16",
        source_count=2,
    )
    entry.source = BlacklistSource.manual
    entry.created_by = admin.id
    state = await materialize_state(db_session)
    desired_revision = state.desired_revision

    await list_service.remove_blacklist(
        db_session,
        source_cidr="185.2.0.0/16",
        actor=admin,
    )

    demoted = await db_session.get(BlacklistEntry, entry.id)
    assertions = list(
        (
            await db_session.scalars(
                select(FeedBlacklistAssertion).where(
                    FeedBlacklistAssertion.blacklist_entry_id == entry.id
                )
            )
        ).all()
    )
    state = await db_session.get(GlobalDenyState, 1)
    assert demoted is not None
    assert demoted.source == BlacklistSource.feed
    assert {assertion.feed_source_id for assertion in assertions} == {
        source.id for source in sources
    }
    assert state is not None
    assert state.desired_revision == desired_revision
    assert (
        await db_session.scalar(
            select(func.count(AgentJob.id)).where(AgentJob.job_type == JobType.global_deny_apply)
        )
    ) == 0


async def test_remove_global_manual_without_assertions_deletes_and_queues_convergence(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "global-remove-admin")
    entry = BlacklistEntry(
        scope=BlacklistScope.global_,
        source=BlacklistSource.manual,
        source_cidr="185.3.0.0/16",
        created_by=admin.id,
    )
    db_session.add(entry)
    await db_session.flush()
    state = await materialize_state(db_session)
    desired_revision = state.desired_revision

    await list_service.remove_blacklist(
        db_session,
        source_cidr="185.3.0.0/16",
        actor=admin,
    )

    state = await db_session.get(GlobalDenyState, 1)
    job = (
        await db_session.scalars(
            select(AgentJob).where(AgentJob.job_type == JobType.global_deny_apply)
        )
    ).one()
    assert await db_session.get(BlacklistEntry, entry.id) is None
    assert state is not None
    assert state.desired_revision == desired_revision + 1
    assert job.version == state.desired_revision


async def test_remove_global_feed_only_entry_conflicts_without_mutating_state(
    db_session: AsyncSession,
) -> None:
    admin = await create_admin(db_session, "global-feed-only-admin")
    entry, sources = await seed_feed_entry(db_session, source_cidr="185.4.0.0/16")
    state = await materialize_state(db_session)
    desired_revision = state.desired_revision
    desired_digest = state.desired_digest

    with pytest.raises(HTTPException) as exc_info:
        await list_service.remove_blacklist(
            db_session,
            source_cidr="185.4.0.0/16",
            actor=admin,
        )

    persisted = await db_session.get(BlacklistEntry, entry.id)
    assertions = list(
        (
            await db_session.scalars(
                select(FeedBlacklistAssertion).where(
                    FeedBlacklistAssertion.blacklist_entry_id == entry.id
                )
            )
        ).all()
    )
    state = await db_session.get(GlobalDenyState, 1)
    assert exc_info.value.status_code == 409
    assert persisted is not None
    assert persisted.source == BlacklistSource.feed
    assert [assertion.feed_source_id for assertion in assertions] == [sources[0].id]
    assert state is not None
    assert (state.desired_revision, state.desired_digest) == (desired_revision, desired_digest)
    assert (
        await db_session.scalar(
            select(func.count(AgentJob.id)).where(AgentJob.job_type == JobType.global_deny_apply)
        )
    ) == 0


async def test_add_global_manual_dispatches_one_convergence_job_after_commit(
    committed_db: async_sessionmaker[AsyncSession],
    redis_client: Redis,
) -> None:
    async with committed_db() as db:
        admin = await create_admin(db, "global-convergence-admin")
        entry = await list_service.add_blacklist(
            db,
            source_cidr="45.0.0.0/8",
            actor=admin,
        )
        state = await db.get(GlobalDenyState, 1)
        job = (
            await db.scalars(select(AgentJob).where(AgentJob.job_type == JobType.global_deny_apply))
        ).one()

        assert state is not None
        assert state.desired_revision == 1
        assert job.version == state.desired_revision
        assert await redis_client.lrange(APPLY_QUEUE_KEY, 0, -1) == []
        await db.commit()
        await run_post_commit_callbacks(db)

    assert entry.source == BlacklistSource.manual
    assert await redis_client.lrange(APPLY_QUEUE_KEY, 0, -1) == [str(job.id)]





async def test_global_blacklist_list_remove_requires_admin(db_session: AsyncSession) -> None:
    admin = await create_admin(db_session, "global-list-admin")
    tenant = await create_tenant(db_session, "Global List Tenant")
    tenant_user = await create_tenant_user(db_session, username="global-list-user", tenant=tenant)
    entry = await list_service.add_blacklist(
        db_session,
        source_cidr="185.0.0.0/8",
        actor=admin,
    )

    listed = await list_service.list_blacklist(
        db_session,
        actor=admin,
    )
    await list_service.remove_blacklist(
        db_session,
        source_cidr="185.0.0.0/8",
        actor=admin,
    )
    with pytest.raises(HTTPException) as exc_info:
        await list_service.list_blacklist(
            db_session,
            actor=tenant_user,
        )

    assert [row.id for row in listed] == [entry.id]
    assert exc_info.value.status_code == 403
