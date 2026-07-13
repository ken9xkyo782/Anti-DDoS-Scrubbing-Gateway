# Control-plane frontend

This Vite application provides the authenticated tenant and administrator
telemetry dashboards. The browser uses the existing cookie-backed auth API and
redirects to **Login** when a request returns `401`.

## Develop and test

From this directory, install dependencies and start the development server:

```sh
npm ci
npm run dev
```

Vite proxies `/auth`, `/services`, and `/node` to the control plane. The tenant
dashboard lists only the services returned by `GET /services`, then polls the
selected service every two seconds. The administrator dashboard polls node
telemetry and health every two seconds. Both dashboards show loading, empty,
error, and stale states; `generic` and `offline` XDP modes are critical.

Run the complete frontend gate before shipping a dashboard change:

```sh
npm run lint
npm run typecheck
npm run test -- --run
npm run build
```

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
