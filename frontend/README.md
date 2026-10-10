# ZendAgent frontend (Next.js)

The new ZendAgent interface. It talks to the existing Python dashboard server; the original dashboard in `UI/` is untouched and stays the fallback.

## Run it locally

1. Start the Python side as usual (`python main.py`, then `python UI/server.py`, which serves on `http://127.0.0.1:8765`).
2. In this folder: `npm install`, then `npm run dev`.
3. Open **http://127.0.0.1:3000/overview/**.

`npm run dev` starts [scripts/dev.mjs](scripts/dev.mjs), a dev-only proxy on port 3000 in front of `next dev` and the Python server. It is used instead of a Next.js rewrite because a rewrite replaces the `Host` header, which disables the backend's DNS-rebinding guard. The proxy:

- refuses any non-local `Host` (same rule as `room_agent.control.local_host`);
- refuses `/api/*` requests whose `Origin` isn't its own origin (stricter than the backend, which accepts any localhost port);
- never adds credentials and forwards everything else untouched, so the backend still enforces `X-Jarvis`, Origin and the executor's safety checks.

Environment variables: `ZEND_DEV_PORT` (default 3000), `ZEND_BACKEND_URL` (default `http://127.0.0.1:8765`), `NEXT_PUBLIC_CLASSIC_URL` (link target for "Original dashboard"), `NEXT_PUBLIC_ZEND_MOCK=1` (fixture data without a backend; display only).

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Dev server with the secure proxy |
| `npm run build` | Static export to `out/` |
| `npm run typecheck` / `npm run lint` | TypeScript and ESLint |
| `npm test` | Vitest and Testing Library suite |

## Structure

- `app/(dashboard)/*`: one thin route per page.
- `features/*`: page logic and views (assistant, settings, connections, memory, missions, activity, status, overview).
- `components/ui`, `components/layout`, `components/providers`: design system, shell, toasts, confirmation dialog, theme.
- `lib/api`: typed client (`client.ts`), outcome handling (`actions.ts`), queries, types, mock fixtures.
- `styles/tokens.css`: the only place brand and theme colors are defined.

## Security model

- All requests are same-origin and relative (`/api/...`). No credentials, tokens or keys are in the bundle; `X-Jarvis: 1` is a marker, not a secret.
- Every protected action goes through the backend's `/api/action`; the UI adds confirmation dialogs (forget memory, disconnect, stop mission, approvals) but never bypasses the server's checks.
- An unknown or unparseable outcome is never reported as success, and drafts are kept when a request fails.

## Deploying (requires approval, not done yet)

`npm run build` produces a static site in `out/`. Serving it from Python needs one additive change to `UI/server.py` (a shared file, so it has not been made): a route such as `GET /app/<path>` that serves `frontend/out/`, behind an opt-in setting. Because it would be same-origin, the existing Host, Origin and `X-Jarvis` guards apply unchanged. The static export uses root-relative links, so serving it under `/app/` also needs `basePath: "/app"` in `next.config.ts`.

## Not yet done

The Futuristic HUD (Phase E) and a Classic/HUD mode switch.
