# Control-plane frontend

This Vite + React single-page application is the operator and tenant console for
the Anti-DDoS Scrubbing Gateway. It has two halves:

- **Observability** — the tenant and administrator telemetry dashboards, billing
  showback, and alert history (milestone M5).
- **Configuration management** — role-aware CRUD screens for services, rules,
  lists, tenants, users, CIDR allocations, threat feeds, alert rules and
  channels, and node controls (feature `AD-034`).

The browser authenticates against the cookie-backed control-plane API and
redirects to **Login** whenever a request returns `401`. Every configuration
screen reads and writes the existing, tested control-plane endpoints — the
frontend adds no backend surface of its own.

## Design system

The console is built on a small, self-contained design system. There is no
external CSS framework.

- **Tokens** — `src/theme/tokens.css` defines CSS custom properties for color,
  spacing, typography, radius, shadow, z-index, and motion. A dark palette is
  provided through both `@media (prefers-color-scheme: dark)` and a
  `:root[data-theme="dark"]` override, so the in-app toggle wins in either
  direction. `src/theme/base.css` supplies the reset and the focus-visible ring.
- **Theme toggle** — `src/theme/useTheme.ts` sets `data-theme` on the document
  root and persists the choice in `localStorage`.
- **Severity colors** — `src/theme/thresholds.ts` maps metric thresholds (from
  the PRD) to `ok`, `warning`, and `critical` colors sourced from the tokens.
- **Primitives** — `src/ui/` provides accessible, token-styled components with
  CSS Modules. Interactive widgets (dialog, dropdown menu, tabs, toast, tooltip,
  select, switch) wrap the unified [`radix-ui`](https://www.radix-ui.com/)
  package for keyboard and screen-reader behavior. Import everything from the
  barrel:

  ```ts
  import { Button, Field, Input, Select, Switch, Dialog, ConfirmDialog,
    DropdownMenu, Tabs, useToast, Tooltip, Card, Badge, StatusBadge,
    EmptyState, Skeleton, Spinner, PageHeader, Pagination, DataTable,
  } from '../ui'
  ```

## Layout and navigation

`src/layout/AppShell.tsx` wraps every authenticated route with a sidebar, a top
bar, and the content area. `src/routes/ProtectedRoute.tsx` guards the tree and
restricts admin routes to the `admin` role; a tenant that requests an admin URL
lands on `/forbidden`.

`src/layout/Sidebar.tsx` renders three role-aware groups:

- **Overview** — the role dashboard (`/tenant` or `/admin`).
- **Manage** — the configuration screens for the current role.
- **Observe** — Telemetry, Billing, and Alerts.

`src/layout/Topbar.tsx` holds the theme toggle, the signed-in user's account
menu, and the apply-status indicator described below.

## Apply-status feedback

Service, rule, and list changes are applied asynchronously: the API accepts a
mutation with `202 Accepted` and a body of `{ apply_status, version,
active_version }`, then the worker propagates the change to the data plane.

The console never presents a change as live before the data plane confirms it:

1. A mutation shows a toast and the affected row displays a `StatusBadge`.
2. `src/hooks/useApplyStatus.ts` polls `GET /services/{id}/apply-status` every
   second while the status is `pending`, `queued`, or `applying`.
3. Polling stops at the terminal `active` or `failed` state. A `failed` apply
   shows the error and the still-live `active_version`.
4. After 30 seconds without a terminal state, the hook slows its polling and
   surfaces a "taking longer than expected" state rather than reporting success.

`src/layout/ApplyStatusIndicator.tsx` aggregates in-flight applies in the top
bar.

## Configuration screens

Each screen maps to control-plane endpoints that already exist. Tenant screens
resolve ownership through the API's per-service guard; admin screens require the
`admin` role.

### Tenant self-service

| Screen | Route | Primary endpoints |
| --- | --- | --- |
| Services | `/services` | `GET/POST /services`, `PATCH/DELETE /services/{id}`, `POST /services/{id}/enable\|disable` |
| Service detail — rules | `/services/:id` | `GET/POST /services/{id}/rules`, `PATCH/DELETE …/rules/{ruleId}`, `POST …/rules/overlap-check` |
| Service detail — whitelist and blacklist | `/services/:id` | `GET/POST/DELETE /services/{id}/whitelist` and `…/blacklist` |
| Allocations (read-only) | `/allocations` | `GET /me/allocations` |

Plan sizing (`committed_clean_gbps` and `ceiling_clean_gbps`) is read-only for
tenants, because the API restricts `PATCH /services/{id}/plan` to admins.

### Admin console

| Screen | Route | Primary endpoints |
| --- | --- | --- |
| Services and plans | `/admin/services` | `GET /services`, `PATCH /services/{id}/plan`, service lifecycle endpoints |
| Tenants | `/admin/tenants` | `GET/POST /tenants`, `PATCH/DELETE /tenants/{id}`, `POST …/suspend\|reactivate` |
| Users | `/admin/users` | `GET/POST /users`, `PATCH/DELETE /users/{id}`, `POST /users/{id}/reset-password` |
| Allocations | `/admin/allocations` | `GET /allocations`, allocation create, `POST /allocations/overlap-check`, `POST /allocations/{id}/revoke` |
| Threat feeds | `/admin/feeds` | `GET/POST/PUT/DELETE /feeds`, `POST /feeds/{id}/sync`, `GET /feeds/{id}/syncs` |
| Global blacklist | `/admin/global-blacklist` | `GET/POST/DELETE /blacklist` |
| Alerting | `/admin/alerting` | `GET/PATCH /alerts/rules`, `GET/POST/PATCH/DELETE /alerts/channels`, `POST /alerts/channels/{id}/test` |
| Node control | `/admin/node` | `GET /node/health`, `POST /node/bypass`, `POST /node/maintenance` |
| Job backlog | `/admin/jobs` | `GET /jobs` |

The account screen (`/account`, both roles) changes the signed-in user's
password through `POST /auth/password`.

## Source layout

| Path | Contents |
| --- | --- |
| `src/theme/` | Design tokens, base styles, theme toggle, and threshold colors |
| `src/ui/` | Reusable design-system primitives |
| `src/layout/` | App shell, sidebar, top bar, and apply-status indicator |
| `src/api/` | `apiClient`, typed error handling, and shared DTO types |
| `src/hooks/` | Telemetry hooks, `useApplyStatus`, and `hooks/resources/` mutation hooks |
| `src/features/config/` | Configuration screens, grouped by resource |
| `src/pages/` | Login and the tenant and admin dashboards |
| `src/components/` | Observability panels and charts |

## Develop and test

From this directory, install dependencies and start the development server:

```sh
npm ci
npm run dev
```

The dev server proxies API calls to the control plane at
`http://127.0.0.1:8000`. `vite.config.ts` forwards every API prefix the SPA uses:
`/auth`, `/billing`, `/services`, `/node`, `/tenants`, `/users`, `/allocations`,
`/me`, `/feeds`, `/blacklist`, `/alerts`, and `/jobs`. Production serving (see
below) is same-origin and needs no proxy.

Run the complete frontend gate before shipping a change:

```sh
npm run lint
npm run typecheck
npm run test -- --run
npm run build
```

Keep component, hook, and route tests beside their source as `*.test.ts(x)`, and
call the typed `apiClient` instead of mocking `fetch` in each screen.

## Deploy with FastAPI

Build the production bundle, then point the control plane at its `dist`
directory:

```sh
npm run build
export CONTROL_PLANE_FRONTEND_STATIC_DIR="$(pwd)/dist"
```

FastAPI mounts built assets and serves `index.html` only for browser-history
routes. API paths and missing asset requests remain 404 responses, so an API
client never receives SPA HTML by mistake.

## Architecture reference

The configuration console is specified in
`.specs/features/config-management-spa/`:

- Requirements: `spec.md` (`CFG-01` through `CFG-53`).
- Design and decisions: `design.md` (`AD-034`).
- Rendered diagrams: `diagrams/config-architecture.svg` (component layers) and
  `diagrams/apply-status-ux.svg` (the async apply-status flow).
