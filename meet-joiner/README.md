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
- `src/app/api/graph/` — server-side proxies to `central-kg-api`
  (`/subgraph`, `/entity/{id}`) so the KG base URL and CORS stay
  server-side. Configure via `KG_API_URL` (see `.env.example`).

## Knowledge-graph explorer (`/graph`)

The admin-style graph panel: search a term → `central-kg-api` returns the
matching subgraph (hybrid OpenSearch→pgvector seed + recursive-CTE
traversal) → it renders as an interactive force graph. Click a node for its
properties, then **Expand neighbors** to grow the view one hop at a time.

Point it at the KG service with `KG_API_URL` (defaults to
`http://localhost:8000`). It needs `central-kg-api` running and a seeded
graph — see [`../central-kg-api`](../central-kg-api).

This is the first panel of a fuller admin dashboard; the live Kafka event
feed + agent/task board are the planned next panels (they need the
`agent-system/gateway` Kafka→WS bridge, currently a stub).

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
