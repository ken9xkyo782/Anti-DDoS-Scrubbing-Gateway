# Threat Intelligence Feed Sync — Context (Discuss output)

**Spec:** `.specs/features/threat-feed-sync/spec.md` (FEED-01..40)
**Captured:** 2026-07-10 (discuss within Specify)
**Status:** Ready for design

---

## Feature Boundary

The **authoritative writer** of `source=feed`, `scope=global` `BlacklistEntry` rows — the last M4
feature, and the fulfilment of AD-011's deferral ("manual global blacklist CRUD ships now; **feed**
auto-population deferred to M4; the `source` field discriminates `manual`/`feed`") and M4 #2's explicit
punt ("feed carry-forward converges both slots' global-deny inners to a shared object; **M4 #3 feed sync
must be slot-aware — its problem**").

- **Owns:** the `ThreatFeedSource` model + `/feeds` CRUD + `POST /feeds/{id}/sync`; the `FEED_SYNC` job
  type and handler (fetch → parse → validate → normalize → dedup → reconcile into global blacklist); the
  in-worker due-time scheduler; the whitelist-overlap flag/alert; the `FeedSyncRun` stats; and — reusing
  M4 #2's helper — the **slot-aware global-deny map rebuild + atomic swap** (inverse carry-forward).
- **Reuses, never reimplements:** the agent-worker runtime + `HANDLERS` registry + `process_job`
  orchestration (M4 #1); the `xdpgw-apply` helper + pinned config/global-deny maps + build→verify→flip
  (M4 #2); the `global_blacklist_bloom`/`lpm` + `gbl_meta` build contract and `BlacklistEntry` consumption
  (AD-023, blacklist-filters VERIFIED); the audit-event and `AgentJob` ledger infra (M1).
- **Does not own:** structured/STIX feed formats, feed-driven whitelist/service-scoped lists, alert
  **delivery** (M6), blocked-port-bitmap population, feed license review (CM-07), M5 dashboards.

**Execute is hard-gated** on **M4 #1 (agent-worker) and M4 #2 (double-buffer) executed** — both are
currently Tasks-approved/drafted, not executed. The control-plane sync core (P1 fetch/validate/dedup/
stats/overlap) needs only the worker; the P1 *Data-plane propagation* story needs the double-buffer
helper.

---

## Implementation Decisions

Three gray areas on the feed↔data-plane and scheduling seams were resolved with the user after the spec
draft (2026-07-10).

### D-FEED-1: Data-plane scope = included — slot-aware global-deny rebuild + swap via M4 #2's helper

**Question:** How far into the data-plane does this feature reach? M4 #2 carries the global-deny maps
forward and is not yet executed, so the actual global-blacklist map rebuild + swap would reuse its
`xdpgw-apply` helper and hard-gate on it.
**Decision:** **Include** the global-deny map rebuild + atomic swap. A successful `FEED_SYNC` that changed
the global-deny set drives a **slot-aware** rebuild of the feed-owned maps (`global_blacklist_bloom`/
`lpm`, `gbl_meta`) into the inactive slot via the M4 #2 `xdpgw-apply` helper, **carrying forward all
service-scoped maps unchanged** — the exact inverse of M4 #2's carry-forward (which rebuilds service maps
and carries the global-deny maps). Commit is the same single atomic `active_config` flip with fail-closed
abort-before-flip (last-active version stays live on any build/verify failure). **Execute is hard-gated
on M4 #2 executed.**
**Why:** A threat feed that never reaches XDP is inert — reaching the hot path is the feature's whole
point. M4 #2 already built the slot-aware apply machinery and *deliberately left the global-deny content
to this feature*; reusing its helper (rather than a second write mechanism) keeps one audited C writer
and one swap protocol. The control-plane sync core is still independently buildable/testable ahead of the
DP path (mirrors the agent-worker placeholder precedent), so the gate localizes to the P1 DP story.
**Trade-off:** this feature cannot fully Execute until M4 #2 lands; feed swaps and per-service swaps now
share one `active_config` version line and one helper (the helper gains a "rebuild-global, carry-service"
mode — the inverse of its default), so slot/version accounting must stay coherent across both writers.

### D-FEED-2: Feed format = plain IPv4/CIDR line lists only

**Question:** Which feed source formats must v1 ingest?
**Decision:** **Plain newline-delimited IPv4 address/CIDR text only** (Spamhaus DROP / Feodo / FireHOL
style) — tolerant of blank lines, full-line and inline comments (`#`, `;`), and surrounding whitespace.
Each line validates as a canonical IPv4 address or CIDR (bare host → `/32`); IPv6, `0.0.0.0/0`, and
non-canonical host-bit CIDRs are rejected and counted invalid. JSON/CSV and STIX/TAXII are **out of
scope** (deferred).
**Why:** Line lists cover the common public reputation feeds with the smallest, most robust parser, and
reuse the existing `ipaddress`-based canonical-IPv4 validation already used for CIDR allocation and list
CRUD (AD-010). Structured/STIX ingest is a large parser surface with per-source schema config, better
suited to a later increment once the sync/apply spine is proven.
**Trade-off:** feeds published only as JSON/STIX aren't ingestible in v1 (documented onboarding
constraint); a `format` column is kept on `ThreatFeedSource` so additional parsers can be added without a
model change.

### D-FEED-3: Scheduling = in-worker per-source due-time scheduler

**Question:** How are scheduled syncs triggered in v1 (manual `POST /feeds/{id}/sync` exists regardless)?
**Decision:** The **agent-worker's existing periodic tick** enqueues `FEED_SYNC` for every enabled source
whose persisted `next_sync_at ≤ now`; after each run `next_sync_at = finished_at + sync_interval`
(failures retry next interval), a source already having an in-flight `FEED_SYNC` is not double-enqueued,
and due sources are caught up on worker restart. No external cron.
**Why:** Self-contained (no out-of-repo operational dependency), reuses the reconcile-loop pattern the
worker already runs, and keeps the schedule state in Postgres (survives restart, visible to the API/dash).
External cron adds an operational surface for no benefit at single-node pilot; manual-only would drop the
"scheduled" identity the feature is named for.
**Trade-off:** sync cadence is bounded below by the worker tick interval (fine at feed-refresh
timescales); a wedged worker stops all scheduled syncs (covered by M6 worker-down alerting, out of scope
here); an enforced interval floor prevents a mis-set tiny interval from hammering upstreams.

---

## Assumptions (flagged for Design)

- **A-FEED-1 — Feeds are node-global, admin-only.** `ThreatFeedSource` and `/feeds` are RBAC-admin;
  feeds are not tenant-scoped or tenant-managed (tenants get scoped whitelist/blacklist via M1 SRL).
- **A-FEED-2 — Feed entries are global-deny only.** Feeds only ever write `BlacklistEntry(scope=global,
  source=feed)` — never whitelist, never `scope=service`.
- **A-FEED-3 — Per-source provenance + precedence.** To remove only a source's stale entries and support
  multi-source dedup while preserving the existing `scope=global` source-CIDR uniqueness, `BlacklistEntry`
  gains a nullable `feed_source_id` FK (feed rows only). Collision precedence: **manual > feed** (feed sync
  never removes/overwrites a manual global entry); a CIDR from multiple feeds collapses to one global row.
  Exact provenance/ownership mechanics (single row + owner set vs. reference-counting) = Design call.
- **A-FEED-4 — "Alert" in v1 = audit + recorded event.** Whitelist-overlap and feed-sync-failure "alerts"
  are audit/alert events + `FeedSyncRun` flags now; email/webhook **delivery** is M6 (consumes them).
- **A-FEED-5 — Fetch = HTTPS GET, capped.** Per-source timeout + max-response-size cap; no auth in the
  baseline, optional bearer/token via an **env-referenced secret name** (never the value, never logged —
  §7.2). httpx `AsyncClient` (AD-008 async stack).
- **A-FEED-6 — Invalid-line policy.** Invalid/uncanonical/IPv6/`0.0.0.0/0` lines are skipped + counted; a
  run with ≥1 valid entry = `partial` (applies the valid subset); zero-valid or all-invalid = `failed`
  (keep last-active). An empty 200 body counts as zero-valid → failed.
- **A-FEED-7 — `FEED_SYNC` job/ledger shape.** Adds `JobType.feed_sync`. `AgentJob.target_id` is today a
  NOT-NULL FK to `protected_service`; a feed job targets a source, not a service — so either the ledger is
  **generalized** (polymorphic `target_type` + nullable/foreign `target_id`) or feed syncs use a
  dedicated path. Stats persist in a `FeedSyncRun` record regardless. Ledger-vs-dedicated = Design call.
- **A-FEED-8 — Scale envelope.** The 1M global-blacklist entry envelope (PROJECT scale) bounds feed sizes;
  a feed that would exceed it fails the run (keep-last-active) rather than building a partial map — reuses
  the AD-023 1M footprint work (`make blbulk`).

---

## Open Questions for Design

1. **`FEED_SYNC` ledger** — generalize `AgentJob` (polymorphic target) vs. a dedicated feed-sync queue/
   record (A-FEED-7). Impacts idempotency keying and the reconcile sweep.
2. **Global-deny apply channel** — the `xdpgw-apply` helper's inverse mode (rebuild-global / carry-service):
   a flag/mode on the existing helper vs. a sibling entrypoint; how `active_config.version` is shared
   coherently between per-service and feed swaps (D-FEED-1 trade-off).
3. **Provenance model** — how per-source removal + manual>feed precedence + multi-source collapse coexist
   with the `uq_blacklist_global_source_cidr` partial unique index (A-FEED-3).
4. **Overlap detection** — SQL set-overlap (`inet && ` / GiST) of feed CIDRs against `whitelist_entry`
   vs. in-worker computation; batch cost at feed scale.
5. **Fetch limits** — concrete timeout / size-cap / interval-floor defaults (env-tunable, D-SLRD-1 posture).
