# Configuration Management SPA (Admin & Tenant) Tasks

**Design**: `.specs/features/config-management-spa/design.md` (AD-034)
**Spec**: `.specs/features/config-management-spa/spec.md` (`CFG-01..53`)
**Status**: In progress

**Track**: single **frontend** track (`control-plane/frontend/`). Test type for every task = **fe-unit** (Vitest beside source). Gate for every code task = **fe** (`cd control-plane/frontend && npm run lint && npm run typecheck && npm run test -- --run && npm run build`). Baseline `B_fe` = current `npm run test` total (≥34 from telemetry+alerting), **pinned live at Execute**; each task's Done-when adds its own tests and keeps the running total monotonic (no silent deletions).

**Parallelism basis**: fe-unit is parallel-safe **only when tasks edit disjoint source files** (the fe gate is independent of `compose.test.yml`; TESTING.md §Gate). Shared-file tasks (theme barrel, shell, service-detail tabs) are **sequential**. `[P]` tasks touch disjoint directories and only *read* the shared foundation — run each in an isolated worktree (or serialize the final whole-project gate) since `typecheck`/`build` compile all of `src/`.

**Endpoint grounding**: every screen maps to an endpoint verified present in `app/api/routers/*` (incl. `/alerts/*` and `/node/bypass|maintenance|health`) — **zero backend change**. A screen that turns out to need a missing endpoint is dropped + `SPEC_DEVIATION`.

---

## Execution Plan

### Phase 1 — Design-system foundation
```
T1 ─→ T2 ─→ T3 ─→ T4      (theme → primitive families; shared ui/ barrel ⇒ serial)
T5 [P] ───────────────    (api layer; disjoint api/ files ⇒ parallel to T1–T4)
```

### Phase 2 — Data layer + app shell
```
T4, T5 ─→ T6 ─→ T7        (apply-status glue → shell/routes)
```

### Phase 3 — Tenant self-service (P1 demoable slice)
```
T5 ─→ T8 [P] ─┐
T7, T8 ───────→ T9 ─→ T10 ─→ T11   (share service feature dir ⇒ serial)
```

### Phase 4 — Admin console (P2, disjoint pages ⇒ parallel)
```
T7 ─┬─→ T12 [P]  (tenants+users)
    ├─→ T13 [P]  (allocations)
    ├─→ T15 [P]  (feeds+global BL)
    ├─→ T16 [P]  (alerting config)
    └─→ T17 [P]  (node control)
T8 ─→ T14 [P]    (admin services + plan; extends useServices)
```

### Phase 5 — P3 + docs
```
T7 ─→ T18 [P]   (account + job backlog)
T11 ─→ T19 [P]  (docs)
```

---

## Task Breakdown

### T1: Design-system foundation (tokens + base + theme wiring)
**What**: Add the `radix-ui` dependency and the CSS-variable token system + reset/base stylesheet, wire them into `main.tsx`, add the light/dark theme mechanism, and fold `thresholds.ts` severity colors into semantic tokens.
**Where**: `frontend/package.json`, `frontend/src/theme/tokens.css`, `frontend/src/theme/base.css`, `frontend/src/main.tsx`, `frontend/src/theme/thresholds.ts` (refactor to reference tokens), `frontend/src/theme/useTheme.ts`
**Depends on**: None
**Reuses**: existing `theme/thresholds.ts` severity colors (`#1a7f37/#9a6700/#b42318`), `main.tsx` provider tree
**Requirement**: CFG-01, CFG-05 (theme persistence), D-034-1/9
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] `radix-ui` (unified) pinned in `package.json`; `npm install` clean; React 19 peer range verified (no `--legacy-peer-deps`)
- [x] `tokens.css` defines color/space/type/radius/shadow/z/motion vars with a dark set via `@media (prefers-color-scheme: dark)` **and** `:root[data-theme="dark"]`; `base.css` resets + focus-visible ring; both imported once in `main.tsx`
- [x] `useTheme()` toggles `data-theme` and persists to `localStorage`; `thresholds.ts` helpers unchanged in API but colors sourced from tokens
- [x] `prefers-reduced-motion` respected; AA contrast in both themes (spot-checked)
- [x] Gate passes: `fe`; adds ≥2 fe-unit tests (useTheme, thresholds), total ≥ `B_fe`+2
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add design tokens, base styles, and theme toggle`

---

### T2: UI primitives — form family
**What**: Build the accessible form primitives (`Button`, `Field`, `Input`, `Textarea`, `NumberInput`, `Select` [Radix], `Switch` [Radix]) with token styling + CSS Modules, exported from the `ui/` barrel.
**Where**: `frontend/src/ui/Button/*`, `Field/*`, `Input/*`, `Textarea/*`, `NumberInput/*`, `Select/*`, `Switch/*`, `frontend/src/ui/index.ts`
**Depends on**: T1
**Reuses**: tokens (T1), `radix-ui` Select/Switch
**Requirement**: CFG-01, CFG-04, D-034-1/5
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] Each primitive renders with variants/sizes, disabled + loading states; `Field` wires label+hint+error to control via `aria-describedby`/`aria-invalid`
- [x] Radix `Select`/`Switch` keyboard + ARIA behavior intact; barrel exports all
- [x] Gate passes: `fe`; adds ≥6 fe-unit tests (roles/keyboard/disabled), total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add form UI primitives`
> Granularity: cohesive primitive **family** in one task (justified, not split) — mirrors sibling `[P]` panel-group tasks.

---

### T3: UI primitives — overlay & navigation family
**What**: Build `Dialog` + `ConfirmDialog`, `DropdownMenu`, `Tabs`, `Toast` + `useToast`/`Toaster`, `Tooltip` on Radix, token-styled.
**Where**: `frontend/src/ui/Dialog/*`, `ConfirmDialog/*`, `DropdownMenu/*`, `Tabs/*`, `Toast/*`, `Tooltip/*`, `ui/index.ts` (append)
**Depends on**: T2 (shared `ui/index.ts` barrel)
**Reuses**: tokens, `radix-ui` Dialog/DropdownMenu/Tabs/Toast/Tooltip, Button (T2)
**Requirement**: CFG-01, CFG-04, CFG-06, D-034-1
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] `Dialog` traps focus + Esc/overlay close (Radix); `ConfirmDialog` takes `tone`/`confirmLabel`/`onConfirm`; `Toaster` uses `aria-live`; `useToast()` API stable
- [x] Gate passes: `fe`; adds ≥5 fe-unit tests (focus trap, confirm callback, toast announce), total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add overlay and navigation UI primitives`
> Granularity: cohesive family (justified, not split).

---

### T4: UI primitives — data-display family
**What**: Build `Card`, `PageHeader`, `DataTable<Row>`, `Badge`, `StatusBadge`, `EmptyState`, `Skeleton`, `Spinner`, `Pagination`.
**Where**: `frontend/src/ui/Card/*`, `PageHeader/*`, `DataTable/*`, `Badge/*`, `StatusBadge/*`, `EmptyState/*`, `Skeleton/*`, `Spinner/*`, `Pagination/*`, `ui/index.ts` (append)
**Depends on**: T3 (shared barrel)
**Reuses**: tokens, DropdownMenu (T3) for row actions, `ApplyStatus` type (from T5 at wire time — `StatusBadge` renders a passed status string, no import cycle)
**Requirement**: CFG-01, CFG-04, D-034-1
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] `DataTable` renders columns/rows with sticky header, sortable columns, per-row action slot, and dedicated loading (Skeleton) / empty (EmptyState) / error states; `StatusBadge` maps each `apply_status` to a semantic token color
- [x] Gate passes: `fe`; adds ≥6 fe-unit tests (sort, empty, loading, status colors), total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add data-display UI primitives`
> Granularity: cohesive family (justified, not split).

---

### T5: Data client enhancement + shared DTO types  [P]
**What**: Enhance `apiClient` to parse FastAPI `{detail}` bodies into `ApiError.detail`, add `fieldErrorsFrom422(detail)`, and add `api/types.ts` DTO interfaces mirroring the response schemas.
**Where**: `frontend/src/api/client.ts` (modify), `frontend/src/api/errors.ts`, `frontend/src/api/types.ts`
**Depends on**: None  ·  **[P]** (disjoint `api/` files; parallel to T1–T4)
**Reuses**: existing `apiClient`/`ApiError` (keep 401→login + 204 handling)
**Requirement**: CFG-03 (types), CFG-07/13/24 (inline errors), D-034-3/8
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] Non-2xx reads JSON `{detail}` (string OR FastAPI validation array) into `ApiError.detail`; `fieldErrorsFrom422` maps `loc`/`msg` → `{field: message}`; 401→login + 204 unchanged
- [x] `types.ts` interfaces (`ApplyStatus`, `ApplyMutationResponse`, `ApplyStatusView`, `ServiceResponse`, `RuleResponse`, `Whitelist/BlacklistEntryResponse`, `Tenant/User/Allocation/Feed/AlertRule/NotificationChannel/NodeHealth/NodeControlState/JobView`) match `app/api/schemas` field-for-field (no invented fields)
- [x] Gate passes: `fe`; adds ≥4 fe-unit tests (detail parse, 422 mapping, 401/204 preserved), total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): parse API error detail and add shared DTO types`

---

### T6: Async apply-status hook + Topbar indicator
**What**: Build `useApplyStatus(serviceId)` (poll `/services/{id}/apply-status`, terminal-state stop, 30s soft-timeout) and the `ApplyStatusIndicator` that aggregates in-flight applies.
**Where**: `frontend/src/hooks/useApplyStatus.ts`, `frontend/src/layout/ApplyStatusIndicator.tsx`
**Depends on**: T4 (StatusBadge), T5 (types + client)
**Reuses**: TanStack Query `refetchInterval`, `StatusBadge`, `apiClient`
**Requirement**: CFG-03, D-034-4
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] Polls every 1s while `apply_status ∈ {pending,queued,applying}`, stops at `active|failed`; after 30s emits `takingLonger` + slows to 5s; never fabricates `active`
- [x] `ApplyStatusIndicator` shows count of in-flight applies; hidden at zero
- [x] Gate passes: `fe`; adds ≥4 fe-unit tests (fake-timer state machine: settle-to-active, settle-to-failed, soft-timeout), total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add async apply-status hook and indicator`

---

### T7: App shell — Sidebar + Topbar + role-aware routing
**What**: Replace `AppLayout` with `AppShell` (Sidebar with role-filtered nav groups + Topbar with breadcrumb, user menu, ThemeToggle, ApplyStatusIndicator), rewire `App.tsx` routes, migrate `NodeControlBanner`, and rehome the existing dashboards under the new shell.
**Where**: `frontend/src/layout/AppShell.tsx`, `Sidebar.tsx`, `Topbar.tsx`, `ThemeToggle.tsx`, `frontend/src/App.tsx` (modify), `frontend/src/layout/AppLayout.tsx` (remove/replace)
**Depends on**: T6 (indicator; transitively T3/T4/T5)
**Reuses**: `AuthContext`/`useAuth`, `ProtectedRoute` (`allowedRoles`), `NodeControlBanner`, `react-router` `NavLink`, DropdownMenu/Tabs (T3), `useTheme` (T1)
**Requirement**: CFG-01, CFG-02, CFG-05, D-034-2/6
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] Sidebar renders only role-permitted items (Overview/Manage[role]/Observe); admin-only routes wrapped in `ProtectedRoute allowedRoles={['admin']}` → `/forbidden`; Topbar has user menu + ThemeToggle + ApplyStatusIndicator
- [x] Existing dashboards/billing/alerts render under the new shell unchanged in behavior; responsive collapse < 1024px
- [x] Gate passes: `fe`; existing route/panel tests stay green; adds ≥4 fe-unit tests (role-filtered nav, admin-route block for tenant, session-expiry redirect), total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): rebuild app shell with sidebar, topbar, and role nav`

---

### T8: Tenant resource hooks (services / rules / lists)  [P]
**What**: Query + mutation hooks for services, allow-rules, and whitelist/blacklist with cache-key invalidation and raw-`ApiError` passthrough.
**Where**: `frontend/src/hooks/resources/useServices.ts`, `useRules.ts`, `useLists.ts`
**Depends on**: T5  ·  **[P]** (disjoint `hooks/resources/` files; parallel to T6/T7)
**Reuses**: `apiClient`, `types.ts` (T5), existing hook idiom (`useServiceTelemetry`)
**Requirement**: CFG-07..24 (data path), D-034-3
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] `useServices` (list/get/create/patch/enable/disable/delete), `useRules` (list/create/patch/delete/overlap-check), `useLists` (whitelist+blacklist list/add/remove); mutations invalidate the right query keys and surface `ApiError`
- [x] Gate passes: `fe`; adds ≥6 fe-unit tests (invalidation + error passthrough per hook), total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add tenant resource query/mutation hooks`

---

### T9: Tenant Services screen (list + create/edit + lifecycle)
**What**: `ServicesPage` (DataTable) + `ServiceForm` (create/edit; plan **read-only** for tenants) + enable/disable/delete with confirm + inline apply-status.
**Where**: `frontend/src/features/config/services/ServicesPage.tsx`, `ServiceForm.tsx`, `ServiceRow.tsx`
**Depends on**: T7, T8
**Reuses**: ui primitives (T2–T4), `useServices` (T8), `useApplyStatus`/`StatusBadge` (T6), `fieldErrorsFrom422` (T5)
**Requirement**: CFG-07..14, D-034-4/7
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] Lists own services; create/edit via Dialog with client CIDR validation and 422→inline field errors; disable warns drop-all + confirm; delete confirms; committed/ceiling shown read-only
- [x] Each mutation shows `StatusBadge` progressing to a terminal state; empty state has a create CTA
- [x] Gate passes: `fe`; adds ≥5 fe-unit tests (create happy, 422 inline, disable-confirm, apply badge, plan read-only), total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add tenant service management screen`

---

### T10: Service detail + Allow-rules tab
**What**: `ServiceDetailPage` (Radix Tabs) + `RulesTab` (priority-ordered DataTable, create/edit/delete, overlap-check preview).
**Where**: `frontend/src/features/config/services/ServiceDetailPage.tsx`, `RulesTab.tsx`
**Depends on**: T9 (shares the services feature dir + `useServices`)
**Reuses**: Tabs (T3), DataTable, `useRules` (T8), apply-status (T6)
**Requirement**: CFG-15..19
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] Rules listed ascending by `priority` (evaluation order visible); create enforces ≤16 + unique-priority client-side then surfaces API rejection; overlap-check preview shows shadow warnings; edit/delete surface apply-status
- [x] Gate passes: `fe`; adds ≥4 fe-unit tests (priority order, overlap warning, >16 guard, apply-status), total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add service detail with allow-rules tab`

---

### T11: Whitelist/VIP + Blacklist tabs
**What**: `WhitelistTab` + `BlacklistTab` on the service-detail page (add/remove with validation + confirm + apply-status).
**Where**: `frontend/src/features/config/services/WhitelistTab.tsx`, `BlacklistTab.tsx`
**Depends on**: T10 (shares `ServiceDetailPage`)
**Reuses**: `useLists` (T8), DataTable, ConfirmDialog, apply-status
**Requirement**: CFG-20..24
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] Whitelist + blacklist entries list/add/remove with client CIDR validation, confirm-on-remove, 422→inline, and apply-status feedback
- [x] Gate passes: `fe`; adds ≥4 fe-unit tests (add validation, remove confirm, api error inline, both lists), total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add whitelist and blacklist tabs`

---

### T12: Admin Tenants + Users pages  [P]
**What**: `TenantsPage` (CRUD + suspend/reactivate) and `UsersPage` (CRUD + reset-password), with their hooks.
**Where**: `frontend/src/features/config/tenants/*`, `frontend/src/features/config/users/*`, `frontend/src/hooks/resources/useTenants.ts`, `useUsers.ts`
**Depends on**: T7  ·  **[P]** (disjoint feature dirs)
**Reuses**: ui primitives, DataTable, ConfirmDialog, `fieldErrorsFrom422`
**Requirement**: CFG-25..29
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] Tenants list/create/patch/delete(confirm)/suspend/reactivate; Users list/create/patch/delete(confirm)/reset-password with role+tenant assignment; blocking API errors (revoke-in-use etc.) surfaced
- [x] Gate passes: `fe`; adds ≥5 fe-unit tests, total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add admin tenant and user management`

---

### T13: Admin CIDR Allocations page  [P]
**What**: `AllocationsPage` (allocate w/ overlap-check, list w/ usage, revoke w/ confirm) + tenant read of `/me/allocations`; `useAllocations` hook.
**Where**: `frontend/src/features/config/allocations/*`, `frontend/src/hooks/resources/useAllocations.ts`
**Depends on**: T7  ·  **[P]** (disjoint feature dir)
**Reuses**: ui primitives, DataTable, overlap-check pattern
**Requirement**: CFG-30..33
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] Allocate runs overlap-check before commit; revoke confirms and surfaces revoke-in-use (`TCA-16`) block; tenant sees own allocations read-only
- [x] Gate passes: `fe`; adds ≥4 fe-unit tests, total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add admin CIDR allocation management`

---

### T14: Admin Services oversight + plan sizing  [P]
**What**: `AdminServicesPage` (all-tenant table + tenant filter), admin create-with-`tenant_id` (+ optional plan), and admin-only plan sizing via `PATCH /services/{id}/plan`; extend `useServices` with the plan mutation.
**Where**: `frontend/src/features/config/services-admin/*`, `frontend/src/hooks/resources/useServices.ts` (extend)
**Depends on**: T8  ·  **[P]** (disjoint page dir; `useServices` not touched by other Phase-4 `[P]` tasks)
**Reuses**: `useServices` (T8), DataTable, ServiceForm patterns (T9), apply-status
**Requirement**: CFG-34..37, D-034-7
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] Lists services across tenants with filter + owning tenant; admin create requires `tenant_id` and may set plan inline; plan edit hits `/plan` and shows apply-status; enable/disable/delete work for any service
- [x] Gate passes: `fe`; adds ≥4 fe-unit tests (all-tenant list, tenant_id required on admin create, plan patch), total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add admin service oversight and plan sizing`

---

### T15: Admin Threat Feeds + Global Blacklist  [P]
**What**: `FeedsPage` (CRUD + manual sync + sync history) + `GlobalBlacklistPage` (add/list/remove); `useFeeds`/`useGlobalBlacklist` hooks.
**Where**: `frontend/src/features/config/feeds/*`, `frontend/src/features/config/global-blacklist/*`, `frontend/src/hooks/resources/useFeeds.ts`, `useGlobalBlacklist.ts`
**Depends on**: T7  ·  **[P]** (disjoint feature dirs)
**Reuses**: ui primitives, DataTable, ConfirmDialog
**Requirement**: CFG-38..42
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] Feed CRUD with URL + interval-range validation; manual `POST /feeds/{id}/sync` reflects the run; sync history from `/feeds/{id}/syncs`; global blacklist add/list/remove(confirm)
- [x] Gate passes: `fe`; adds ≥5 fe-unit tests, total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add admin threat-feed and global-blacklist management`

---

### T16: Admin Alerting configuration  [P]
**What**: `AlertingPage` — Rules tab (threshold override `PATCH /alerts/rules/{key}`) + Channels tab (CRUD, **write-only** secret, test-send); `useAlertRules`/`useNotificationChannels` hooks.
**Where**: `frontend/src/features/config/alerting/*`, `frontend/src/hooks/resources/useAlertRules.ts`, `useNotificationChannels.ts`
**Depends on**: T7  ·  **[P]** (disjoint feature dir). Endpoints verified present in `app/api/routers/alerts.py`.
**Reuses**: Tabs, ui primitives, DataTable
**Requirement**: CFG-43..46
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] Rules list + per-rule threshold override; channels list/create/patch/delete(confirm); secret field write-only (never renders stored value); test-send shows result
- [x] Gate passes: `fe`; adds ≥4 fe-unit tests (threshold patch, write-only secret, test-send result), total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add admin alerting configuration`

---

### T17: Admin Node Control (bypass + maintenance)  [P]
**What**: `NodeControlPage` — bypass + maintenance toggles with confirm + explanation, reading `/node/health`; `useNodeControl` hook.
**Where**: `frontend/src/features/config/node/*`, `frontend/src/hooks/resources/useNodeControl.ts`
**Depends on**: T7  ·  **[P]** (disjoint feature dir). Endpoints verified present in `app/api/routers/telemetry.py`.
**Reuses**: ui primitives, ConfirmDialog, existing `NodeControlBanner` (unchanged)
**Requirement**: CFG-47..50
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] Shows desired/effective bypass+maintenance from `/node/health`; bypass toggle confirms ("disables scrubbing") → `POST /node/bypass`; maintenance toggle explains queue-and-apply-on-exit → `POST /node/maintenance`; banner still shows active state
- [x] Gate passes: `fe`; adds ≥4 fe-unit tests (state read, bypass confirm, maintenance explain), total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add admin node bypass and maintenance controls`

---

### T18: Account change-password + admin job backlog (P3)  [P]
**What**: `AccountPage` (change password via `POST /auth/password`) + `JobBacklogPage` (`GET /jobs`) + failed-apply detail surfaced via `useApplyStatus`.
**Where**: `frontend/src/features/config/account/*`, `frontend/src/features/config/jobs/*`, `frontend/src/hooks/resources/useJobs.ts`
**Depends on**: T7 (+ T6 apply-status)  ·  **[P]** (disjoint feature dirs)
**Reuses**: ui primitives, `useApplyStatus`
**Requirement**: CFG-51..53
**Tools**: Skill `coding-guidelines`; MCP: none
**Done when**:
- [x] Change-password validates new password client-side + surfaces API errors; job backlog lists jobs with state/target; a `failed` service exposes `last_error` + still-live `active_version`
- [x] Gate passes: `fe`; adds ≥3 fe-unit tests, total monotonic
**Tests**: fe-unit · **Gate**: fe
**Commit**: `feat(config-spa): add account password change and job backlog`

---

### T19: Docs  [P]
**What**: Frontend config-screens doc (nav IA, screen→endpoint map, design-system tokens/primitives, apply-status UX) + TESTING.md note if fe conventions extended.
**Where**: `control-plane/frontend/README.md` (or `docs/`), `.specs/codebase/TESTING.md` (only if fe conventions change)
**Depends on**: T11 (P1 slice complete; documents P2/P3 as they land)  ·  **[P]** (docs only, no code)
**Reuses**: design.md content
**Requirement**: traceability/docs
**Tools**: Skill `docs-writer`; MCP: none
**Done when**:
- [ ] Doc covers foundation, primitives, shell/IA, apply-status UX, and the screen→endpoint map; links the two rendered diagrams
- [ ] No code change → `fe` lint/typecheck/build still green if any `src` doc-comment touched; else N/A
**Tests**: none (docs) · **Gate**: fe (only if `src` touched) / none
**Commit**: `docs(config-spa): document configuration screens and design system`

---

## Pre-Approval Validation

### Check 1 — Task Granularity

| Task | Scope | Status |
| --- | --- | --- |
| T1 | tokens+base+theme wiring (cohesive foundation) | ✅ |
| T2 | form primitive family | ⚠️→✅ cohesive family (justified, not split) |
| T3 | overlay/nav primitive family | ⚠️→✅ cohesive family (justified) |
| T4 | data-display primitive family | ⚠️→✅ cohesive family (justified) |
| T5 | api client + types (disjoint api/) | ✅ |
| T6 | 1 hook + 1 indicator | ✅ |
| T7 | app shell (Sidebar+Topbar+routing) — 1 cohesive shell | ⚠️→✅ cohesive shell (justified) |
| T8 | 3 resource hooks (1 file each, cohesive) | ✅ |
| T9 | 1 screen (services) | ✅ |
| T10 | detail page + rules tab | ✅ |
| T11 | whitelist+blacklist tabs | ✅ |
| T12 | tenants+users pages | ⚠️→✅ paired admin CRUD (cohesive) |
| T13 | 1 page (allocations) | ✅ |
| T14 | 1 page + useServices extend | ✅ |
| T15 | feeds+global-BL pages | ⚠️→✅ paired admin CRUD (cohesive) |
| T16 | 1 page (alerting, 2 tabs) | ✅ |
| T17 | 1 page (node control) | ✅ |
| T18 | account + job backlog (small P3) | ✅ |
| T19 | docs | ✅ |

### Check 2 — Diagram ↔ `Depends on` Cross-Check

| Task | `Depends on` (body) | Diagram arrows | Status |
| --- | --- | --- | --- |
| T1 | None | (root) | ✅ |
| T2 | T1 | T1→T2 | ✅ |
| T3 | T2 | T2→T3 | ✅ |
| T4 | T3 | T3→T4 | ✅ |
| T5 | None `[P]` | (root, parallel) | ✅ |
| T6 | T4, T5 | T4→T6, T5→T6 | ✅ |
| T7 | T6 | T6→T7 | ✅ |
| T8 | T5 `[P]` | T5→T8 | ✅ |
| T9 | T7, T8 | T7→T9, T8→T9 | ✅ |
| T10 | T9 | T9→T10 | ✅ |
| T11 | T10 | T10→T11 | ✅ |
| T12 | T7 `[P]` | T7→T12 | ✅ |
| T13 | T7 `[P]` | T7→T13 | ✅ |
| T14 | T8 `[P]` | T8→T14 | ✅ |
| T15 | T7 `[P]` | T7→T15 | ✅ |
| T16 | T7 `[P]` | T7→T16 | ✅ |
| T17 | T7 `[P]` | T7→T17 | ✅ |
| T18 | T7 `[P]` | T7→T18 | ✅ |
| T19 | T11 `[P]` | T11→T19 | ✅ |

No `[P]` task in a phase depends on another `[P]` task in the same phase (Phase-4 `[P]` tasks each depend only on T7/T8, not on each other; T14's `useServices` edit is disjoint from T12/T13/T15/T16/T17). ✅

### Check 3 — Test Co-location Validation

| Task | Code layer | Matrix requires | Task says | Status |
| --- | --- | --- | --- | --- |
| T1–T18 | `control-plane/frontend/src/` (components/hooks/routes/api-client) | **fe-unit** (Vitest beside source) | fe-unit | ✅ |
| T19 | docs (+ optional `src` doc-comment) | none | none (fe only if `src` touched) | ✅ |

Every code task writes its own fe-unit tests in the same task and runs the **fe** gate; no test deferral. ✅

---

## Tools (per skill step 6 — confirm or adjust)

- **All code tasks (T1–T18)**: Skill `coding-guidelines`; **no MCP**.
- **T19**: Skill `docs-writer`.
- Diagrams already rendered via `mermaid-studio` (design phase).
- No Context7/other MCP needed (React/TanStack/Radix are in-repo or web-verified).

---

## Notes

- **Baselines pinned live at Execute**: `B_fe` = `npm run test` head total (≥34). Each task keeps the total monotonic and states its added-test floor.
- **Radix pin**: T1 pins the exact `radix-ui` version + verifies the React 19 peer range (no `--legacy-peer-deps`); if a Radix component's import path differs under the unified package, adjust at T2/T3.
- **Regression guard**: existing telemetry/billing/alerts panel + route tests must stay green through T7's shell rebuild (they assert text/roles, not pixels); any DOM-structure-coupled assertion touched is a minimal, flagged update within T7.
- **Critical path**: T1→T4 (+T5 `[P]`) → T6 → T7 unlocks everything; T7→T8→T9→T10→T11 is the demoable **P1** slice; Phase-4 admin pages fan out `[P]`; P3+docs last.
- **Independent** of the M6 #3 SLA/OLA backend thread.
