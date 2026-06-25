# meet-joiner

> The sun never sets on this meeting.

A minimal Next.js front-end that dispatches an agent to a Google Meet.
Paste a link, click **Send the envoy**, and the joiner is on its way
across the tundra.

Themed for Sunstead · Lapland · 66°33′N — a liquid midnight sun orbits
the horizon over a layered pine forest.

## What's in here

- `src/app/page.tsx` — single-input client form, joining/joined/error states.
- `src/app/api/join/route.ts` — POST endpoint. Validates the Google Meet
  link shape (`meet.google.com/xxx-xxxx-xxx`) and returns the meeting id.
  **The actual agent dispatch is a `TODO`** — wire in Recall.ai, a
  headless browser, or whichever joiner you prefer.
- `src/app/globals.css` — orbit + morph keyframes for the midnight sun.
- Landscape is an inline SVG inside `page.tsx` (layered hills + pines).
- `src/app/graph/` — **knowledge-graph explorer** (`/graph`). A
  zero-dependency canvas force-directed graph over `central-kg-api`.
  `ForceGraph.tsx` is the renderer (repulsion + link springs + centering,
  drag/zoom/pan, hover highlight, click-to-select); `page.tsx` is the
  search + detail-panel + legend shell; `types.ts` mirrors the KG models.
- `src/app/dashboard/` — **agent dashboard** (`/dashboard`). Live event
  feed + task status board + ask box, over the `agent-system` gateway.
  `useStream.ts` is the auto-reconnecting WS hook; `AskBox.tsx` dispatches
  tasks; `page.tsx` folds the stream into a per-task board.
- `src/app/api/graph/` — server-side proxies to `central-kg-api`
  (`/subgraph`, `/entity/{id}`) so the KG base URL and CORS stay
  server-side. Configure via `KG_API_URL` (see `.env.example`).
- `src/app/api/tasks/` — server-side proxy to the gateway's `POST /tasks`
  (the ask box). Configure via `GATEWAY_URL`.

## Knowledge-graph explorer (`/graph`)

The admin-style graph panel: search a term → `central-kg-api` returns the
matching subgraph (hybrid OpenSearch→pgvector seed + recursive-CTE
traversal) → it renders as an interactive force graph. Click a node for its
properties, then **Expand neighbors** to grow the view one hop at a time.

Point it at the KG service with `KG_API_URL` (defaults to
`http://localhost:8000`). It needs `central-kg-api` running and a seeded
graph — see [`../central-kg-api`](../central-kg-api).

## Agent dashboard (`/dashboard`)

The admin surface over the live system. It opens a WebSocket to the
`agent-system` gateway's `WS /stream` and renders:

- **Live feed** — every `agent.results` / `agent.activity` envelope as it
  arrives (newest first, ring-buffered).
- **Task board** — the same stream folded into one card per `task_id`
  (status, detail, result JSON, artifact links like a deployed URL).
- **Ask box** — pick an intent + JSON args and dispatch a `task.create`
  via `POST /api/tasks` → the gateway → Kafka.

It's **resilient by design**: if the gateway isn't up yet, the WS hook sits
in `connecting` and reconnects with backoff — the page is usable offline and
fills in once the gateway comes online. Connection state shows in the header.

Run the gateway with `make gateway` in [`../agent-system`](../agent-system)
(port 8800). Configure `GATEWAY_URL` (server-side, for the ask box) and
`NEXT_PUBLIC_GATEWAY_WS_URL` (client-side, for the feed) — see `.env.example`.

> **Practicalities:** the local end-to-end path (dashboard `/api/tasks` → gateway →
> local Kafka/redpanda → agent-runner → `WS /stream`) has been **run and proven
> locally** (see `docs/LOG.md`). Still to patch for production: `wss://` + TLS on
> the gateway (the browser opens the WS directly), CORS/auth, and the switch from
> local redpanda to the now-provisioned **Aiven Kafka**. The graceful-degradation
> above means the UI won't crash while a backend is down.

## Run it

```bash
npm install
npm run dev
```

Open <http://localhost:3000>.

## Build

```bash
npm run build
npm start
```

## Wiring up a real joiner

The POST handler at `src/app/api/join/route.ts` currently just validates
the link and echoes back the meeting id. Replace the `TODO` block with
your dispatch of choice, e.g.:

- **Recall.ai** — POST to the bot endpoint with the Meet URL and bot config.
- **Headless browser** — kick off a Playwright / Puppeteer worker in a
  queue and return a job id.
- **Vercel Sandbox** — spin up an ephemeral microVM running the joiner.

The UI is wired to surface the response: as long as your endpoint
returns `{ ok: true, meetingId }` (or an `{ error }` payload), the form
will display the right state.

## Stack

- Next.js 16 (App Router, Turbopack)
- React 19, TypeScript
- Tailwind CSS v4
