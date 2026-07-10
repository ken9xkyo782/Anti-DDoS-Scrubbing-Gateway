# Threat Intelligence Feed Sync Specification

**Feature:** M4 #3 — Threat intelligence feed sync
**Context:** `.specs/features/threat-feed-sync/context.md` (D-FEED-1..3, A-FEED-1..8)
**Status:** Draft (awaiting approval → Design)
**Depends on (Execute-gated):**
- **agent-worker executed** (M4 #1) — provides the long-running worker, `HANDLERS` registry (this
  feature adds `JobType.feed_sync`), `process_job`/reconcile orchestration, and the periodic tick this
  feature's scheduler hooks into.
- **double-buffer executed** (M4 #2) — provides the `xdpgw-apply` C helper, the pinned config/global-deny
  maps, and the slot-aware build→verify→atomic-swap this feature reuses for the global-deny rebuild
  (D-FEED-1). The P1 *Data-plane propagation* story is **hard-gated** on it.
- **blacklist-filters executed** (M3, VERIFIED) — the `global_blacklist_bloom`/`lpm` + `gbl_meta` build
  contract (AD-023) and `BlacklistEntry(scope=global, source=feed)` rows this feature is the authoritative
  writer for (AD-011 deferred feed population to here).

---

## Problem Statement

The global blacklist maps exist and are enforced (M3), and M4 #2 **carries them forward** across every
per-service swap but never writes their content — it deliberately punted "feed sync must be slot-aware —
its problem" to this feature. Today the only writer of global-deny entries is a manual admin CRUD path
and a loader env seed; there is no way to keep a node's global blacklist current from external threat-
intelligence sources. This feature is the authoritative writer of `source=feed` global blacklist entries:
it fetches configured feed sources on a schedule, validates/normalizes/dedups their IP/CIDR lists,
reconciles them into the global blacklist, and drives a slot-aware global-deny map rebuild + atomic swap
so the entries actually reach the XDP hot path — resiliently **per source** (one bad feed never corrupts
the active set) and with sync statistics recorded for observability.

## Goals

- [ ] An admin can register threat-feed sources and have their IP/CIDR lists **fetched, validated,
      deduped, and enforced** as global blacklist entries — automatically on a per-source schedule and
      on manual demand.
- [ ] Sync is **resilient per source**: a fetch/parse/validation failure on one source **keeps that
      source's last-active entries** intact and never affects another source or the active data-plane
      version (the M4 milestone target: "feed sync resilient per source; feed sync records stats").
- [ ] A feed entry overlapping a tenant whitelist raises a **flag + alert** and is **never removed from
      the global map** (tenant isolation, AD-003 / BL-01/02).
- [ ] Every sync run **records statistics** (fetched / valid / added / removed / skipped / overlap /
      duration / status) for the M5 feed-status dashboard and M6 feed-sync-fail alerting.
- [ ] A successful sync **reaches the XDP hot path** via a slot-aware global-deny rebuild + atomic swap,
      reusing M4 #2's helper without disturbing per-service config.

## Out of Scope

| Feature | Reason |
| --- | --- |
| JSON / CSV / STIX / TAXII feed formats | v1 = plain IPv4/CIDR line lists only (D-FEED-2); structured/STIX ingest is a later increment / GA-tier |
| Feed-driven **whitelist** or **service-scoped** blacklist | Feeds populate **global-deny only** (A-FEED-2); scoped lists stay manual (M1 SRL) |
| Automatic **global removal** on whitelist overlap | AD-003 forbids it — overlap flags + alerts, the global entry stays (FEED-22) |
| Email / webhook **alert delivery** | M6 alerting consumes the audit/alert events this feature emits (FEED-23) |
| Per-tenant / tenant-managed feed subscriptions | Feeds are node-global, admin-only (A-FEED-1) |
| `udp_blocked_port_bitmap` population from feeds | Seed-only writer in v1 (D-BLK-2); a blocked-port writer is a separate deferred idea |
| Feed **license / legal** review | Non-engineering (CM-07, Product/Legal); operational gate, not a build task |
| Telemetry dashboard rendering of feed status | M5 consumes the `FeedSyncRun` records / source status fields this feature persists |

---

## User Stories

### P1: Threat feed source management ⭐ MVP

**User Story:** As a **system admin**, I want to register, edit, and remove threat-feed sources so that
the node's global blacklist is driven by curated external intelligence.

**Why P1:** Nothing can sync without a source to sync from; feeds are node-global admin-owned config.

**Acceptance Criteria:**

1. WHEN an admin `POST /feeds` with a name, HTTPS url, and sync interval THEN the system SHALL create a
   `ThreatFeedSource` (enabled, format `line_list`) and return it, rejecting a non-HTTPS url, a duplicate
   name, or an out-of-bounds interval.
2. WHEN an admin `GET /feeds` or `GET /feeds/{id}` THEN the system SHALL return sources with their
   current `last_status` / `last_error` / `last_sync_at` / `next_sync_at`.
3. WHEN an admin `PUT /feeds/{id}` to change url / interval / enabled / credential THEN the system SHALL
   apply it; disabling SHALL stop scheduling but SHALL NOT remove already-synced entries.
4. WHEN an admin `DELETE /feeds/{id}` THEN the system SHALL remove the source and **its** feed-owned
   global entries, record a dangerous-action audit event, and trigger a data-plane rebuild (FEED-24).
5. WHEN any feed source is created / updated / deleted THEN the system SHALL write an audit event (§7.3).
6. WHEN a source carries a fetch credential THEN the system SHALL never log or return it in plaintext
   (§7.2) — it is stored as an env-referenced secret name, not the secret value.
7. WHEN a non-admin calls any `/feeds` endpoint THEN the system SHALL fail closed (403, no partial data).

**Independent Test:** Create a source via API, read it back, update its interval, delete it — asserting
audit events and that a credential value never appears in any response or log.

---

### P1: Resilient per-source sync core ⭐ MVP

**User Story:** As a **system admin**, I want each feed fetched, validated, normalized, and deduped into
the global blacklist so that malicious ranges are blocked — with a bad feed never wiping good data.

**Why P1:** This is the feature — the fetch→validate→dedup→reconcile pipeline with per-source resilience.

**Acceptance Criteria:**

1. WHEN `POST /feeds/{id}/sync` is called THEN the system SHALL enqueue a `FEED_SYNC` job (202) routed
   through the worker; it SHALL be idempotent under duplicate delivery (no double-apply).
2. WHEN a `FEED_SYNC` job runs THEN the system SHALL fetch the source url via HTTPS GET with a per-source
   timeout and a maximum response-size cap.
3. WHEN parsing the response THEN the system SHALL accept a plain line list — tolerating blank lines,
   full-line and inline comments (`#`, `;`), and surrounding whitespace.
4. WHEN validating each entry THEN the system SHALL accept only canonical IPv4 addresses/CIDRs — rejecting
   IPv6, `0.0.0.0/0`, and non-canonical host-bit CIDRs — and normalize a bare host IP to `/32`.
5. WHEN a line is invalid THEN the system SHALL skip and count it (never fail the whole run for it).
6. WHEN the same CIDR appears multiple times in one feed THEN the system SHALL collapse it (within-feed dedup).
7. WHEN reconciling valid entries THEN the system SHALL insert new `BlacklistEntry(scope=global,
   source=feed)` rows for this source, remove this source's entries no longer present, and leave unchanged
   entries untouched.
8. WHEN a CIDR is also asserted by a **manual** global entry THEN feed sync SHALL NOT remove or overwrite
   it (manual precedence); WHEN asserted by multiple feeds THEN it SHALL collapse to a single global row
   (the `scope=global` source-CIDR uniqueness is preserved).
9. WHEN one source's fetch or parse fails THEN the system SHALL NOT affect any other source's entries or
   the active data-plane version (per-source isolation).
10. WHEN a fetch fails, times out, exceeds the size cap, returns a non-2xx status, or yields zero valid
    entries THEN the run SHALL be marked **failed**, the source's previously-synced entries SHALL remain
    intact (no wipe), and the error SHALL be recorded (**keep last-active**).
11. WHEN a sync re-fetches byte-identical content THEN the reconcile SHALL be a no-op (added = removed = 0).

**Independent Test:** Point a source at a static list, sync, assert the global feed entries; add/remove
lines, re-sync, assert the delta; then serve a 500 / oversize / all-invalid body and assert the prior
entries survive and the run is `failed`.

---

### P1: Whitelist-overlap flag & alert (no global removal) ⭐ MVP

**User Story:** As a **system admin**, I want to be alerted when a feed lists a range a tenant has
whitelisted, without that whitelist silently punching a hole in global protection for everyone else.

**Why P1:** Mandatory tenant-isolation safety (AD-003, risk register BL-01/02) — a feed must not be
neutralizable and a whitelist must not weaken global defense.

**Acceptance Criteria:**

1. WHEN reconciling feed entries THEN the system SHALL detect any feed CIDR overlapping an existing
   `whitelist_entry.source_cidr` (across all services).
2. WHEN an overlap is found THEN the system SHALL record a flag on the sync run and emit an audit/alert
   event identifying the feed source, the CIDR, and the overlapping whitelist/service.
3. WHEN an overlap is found THEN the system SHALL still add/keep the global feed entry (never remove or
   skip it) — the owning tenant's scoped whitelist already bypasses it only for that tenant's service.
4. WHEN emitting the overlap event THEN the system SHALL include no secret/PII beyond the CIDRs and SHALL
   make it consumable by M6 alerting.

**Independent Test:** Whitelist a CIDR on a service, sync a feed that lists an overlapping CIDR, assert an
overlap audit/alert event + sync-run flag AND that the global feed entry is present.

---

### P1: Data-plane global-deny rebuild & atomic swap ⭐ MVP *(gated on M4 #2 executed)*

**User Story:** As a **system admin**, I want a successful feed sync to actually block the listed ranges
at the XDP layer, without disturbing any per-service configuration.

**Why P1:** A threat feed that never reaches the data-plane is inert; this is the point of the feature.

**Acceptance Criteria:**

1. WHEN a `FEED_SYNC` changes the global-deny set THEN the system SHALL rebuild the feed-owned global-deny
   maps (`global_blacklist_bloom` / `global_blacklist_lpm` / `gbl_meta`) into the inactive slot via M4 #2's
   `xdpgw-apply` helper and commit with a single atomic `active_config` flip.
2. WHEN rebuilding for a feed sync THEN the system SHALL **carry forward** all service-scoped maps
   unchanged (the inverse of M4 #2's carry-forward) — a feed swap SHALL NOT disturb per-service config.
3. WHEN the build or structural verify fails THEN the system SHALL NOT flip (the last-active data-plane
   version stays live) — fail-closed, mirroring DBS rollback.
4. WHEN a sync produced no net change THEN the system SHALL perform no swap.
5. WHEN a feed would push the global blacklist past the 1M-entry envelope THEN the run SHALL fail
   (keep-last-active) rather than build a partial/corrupt map.
6. WHEN a swap commits THEN the applied node map version SHALL advance and the feed entries SHALL be
   reachable on the XDP hot path.

**Independent Test:** With M4 #2 loaded, sync a feed, assert (via the DP verdict harness / dpstat) that a
listed source is dropped and per-service maps are unchanged; force a build failure and assert the prior
slot stays live.

---

### P2: Scheduled in-worker due-time sync

**User Story:** As a **system admin**, I want feeds to refresh automatically on their configured interval
so the blacklist stays current without manual triggering.

**Why P2:** Automation layered on the P1 manual sync core; the reusable sync path is proven first.

**Acceptance Criteria:**

1. WHEN the worker's periodic tick fires THEN the system SHALL enqueue a `FEED_SYNC` for every **enabled**
   source whose `next_sync_at ≤ now`.
2. WHEN a sync finishes (success or failure) THEN the system SHALL set `next_sync_at = finished_at +
   sync_interval` (a failed source retries on its next interval).
3. WHEN a source is disabled THEN it SHALL never be auto-scheduled (manual `/sync` still works).
4. WHEN a source already has an in-flight `FEED_SYNC` THEN the tick SHALL NOT double-enqueue it.
5. WHEN the worker restarts THEN due sources SHALL be caught up on startup (`next_sync_at` persisted).

**Independent Test:** Set a short interval, advance/await the tick, assert exactly one sync enqueued per
due source and `next_sync_at` advanced; disable a source and assert it is skipped.

---

### P3: Sync observability & operator controls

**User Story:** As a **system admin**, I want to see each source's sync history and validate a feed
without applying it.

**Why P3:** Operator ergonomics and the data surface M5 dashboards render — not required to enforce feeds.

**Acceptance Criteria:**

1. WHEN a sync runs THEN the system SHALL persist a `FeedSyncRun` record: source, started/finished,
   status (`success` / `partial` / `failed`), fetched-lines, valid, added, removed, skipped-invalid,
   overlap-count, `duration_ms`, error.
2. WHEN an admin `GET /feeds/{id}/syncs` THEN the system SHALL return recent sync-run history.
3. WHEN a run has invalid lines but ≥1 valid entry THEN it SHALL be marked `partial` and SHALL apply the
   valid subset.
4. WHEN an admin requests a dry-run sync THEN the system SHALL report parse/validation stats without
   mutating entries or the data-plane.
5. WHEN a sync runs THEN the system SHALL emit a structured JSON log (source, counts, duration) with no
   secrets/PII.

**Independent Test:** Sync a mixed valid/invalid feed, assert a `partial` `FeedSyncRun` with correct
counts via `GET /feeds/{id}/syncs`; dry-run and assert no entries/maps changed.

---

## Edge Cases

- WHEN a fetch returns a redirect chain or non-2xx status THEN the system SHALL treat it as a failed run
  (keep last-active) rather than following/parsing arbitrary bodies.
- WHEN a response exceeds the size cap mid-stream THEN the system SHALL abort the fetch and fail the run.
- WHEN a previously-populated source returns an empty (0-line) body THEN the system SHALL treat it as
  failed (zero-valid → keep last-active) — an empty 200 is indistinguishable from a broken feed; an
  intentionally-empty source is retired by deleting it.
- WHEN a feed lists a CIDR that contains/overlaps another feed's entry THEN both SHALL be kept (LPM/bloom
  handle containment); dedup is exact-match only.
- WHEN a source is deleted while its `FEED_SYNC` is in flight THEN the job SHALL no-op safely.
- WHEN a configured interval is below the enforced minimum THEN the system SHALL clamp it (documented
  floor) to avoid hammering upstreams.
- WHEN IPv6 or malformed lines appear in an IPv4 feed THEN they SHALL be skipped and counted, never fatal.
- WHEN two feeds are synced concurrently THEN each SHALL reconcile independently and the global map SHALL
  reflect the union (no lost update).

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| FEED-01 | P1: Feed source management | Design | Pending |
| FEED-02 | P1: Feed source management | Design | Pending |
| FEED-03 | P1: Feed source management | Design | Pending |
| FEED-04 | P1: Feed source management | Design | Pending |
| FEED-05 | P1: Feed source management | Design | Pending |
| FEED-06 | P1: Feed source management | Design | Pending |
| FEED-07 | P1: Feed source management | Design | Pending |
| FEED-08 | P1: Resilient sync core | Design | Pending |
| FEED-09 | P1: Resilient sync core | Design | Pending |
| FEED-10 | P1: Resilient sync core | Design | Pending |
| FEED-11 | P1: Resilient sync core | Design | Pending |
| FEED-12 | P1: Resilient sync core | Design | Pending |
| FEED-13 | P1: Resilient sync core | Design | Pending |
| FEED-14 | P1: Resilient sync core | Design | Pending |
| FEED-15 | P1: Resilient sync core | Design | Pending |
| FEED-16 | P1: Resilient sync core | Design | Pending |
| FEED-17 | P1: Resilient sync core | Design | Pending |
| FEED-18 | P1: Resilient sync core | Design | Pending |
| FEED-19 | P1: Whitelist-overlap flag | Design | Pending |
| FEED-20 | P1: Whitelist-overlap flag | Design | Pending |
| FEED-21 | P1: Whitelist-overlap flag | Design | Pending |
| FEED-22 | P1: Whitelist-overlap flag | Design | Pending |
| FEED-23 | P1: Whitelist-overlap flag | Design | Pending |
| FEED-24 | P1: Data-plane rebuild & swap | Design | Pending |
| FEED-25 | P1: Data-plane rebuild & swap | Design | Pending |
| FEED-26 | P1: Data-plane rebuild & swap | Design | Pending |
| FEED-27 | P1: Data-plane rebuild & swap | Design | Pending |
| FEED-28 | P1: Data-plane rebuild & swap | Design | Pending |
| FEED-29 | P1: Data-plane rebuild & swap | Design | Pending |
| FEED-30 | P2: Scheduled sync | Design | Pending |
| FEED-31 | P2: Scheduled sync | Design | Pending |
| FEED-32 | P2: Scheduled sync | Design | Pending |
| FEED-33 | P2: Scheduled sync | Design | Pending |
| FEED-34 | P2: Scheduled sync | Design | Pending |
| FEED-35 | P3: Observability & controls | Design | Pending |
| FEED-36 | P3: Observability & controls | Design | Pending |
| FEED-37 | P3: Observability & controls | Design | Pending |
| FEED-38 | P3: Observability & controls | Design | Pending |
| FEED-39 | P3: Observability & controls | Design | Pending |
| FEED-40 | P3: Observability & controls | Design | Pending |

**ID format:** `FEED-[NUMBER]`
**Status values:** Pending → In Design → In Tasks → Implementing → Verified
**Coverage:** 40 total, 0 mapped to tasks (pre-Design), 0 unmapped ⚠️

> Note: the ID ordering above groups by story; the whitelist-overlap criteria (spec §"Whitelist-overlap
> flag & alert" AC 1–4) map to FEED-19..23 and the resilient-sync-core criteria (AC 1–11) map to
> FEED-08..18 — reconciled precisely at Design when requirements are frozen.

---

## Success Criteria

- [ ] An admin registers a source, and its IP/CIDR list is fetched, deduped, and enforced at the XDP
      hot path (with M4 #2 loaded) — automatically on interval and on manual `/sync`.
- [ ] A failing source (500 / timeout / oversize / all-invalid) leaves its prior entries and the active
      data-plane version untouched, and records a `failed` `FeedSyncRun` with the error.
- [ ] A feed CIDR overlapping a tenant whitelist produces an alert + audit event and the global entry
      remains — zero global removals from overlap.
- [ ] Every sync run is queryable via `GET /feeds/{id}/syncs` with accurate counts and status.
- [ ] No feed credential or PII appears in any API response or log.
