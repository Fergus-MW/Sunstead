# Sunstead — Agent Optimization Research

**Timestamp:** 2026-06-25T04-42Z
**Method:** one research subagent per agent (git/KG, data, web, meeting-ops, research, planner), each given full system context + the fast/reliable/graph-first/plethora-of-items constraints, each tasked to research current (2025–2026) techniques online and propose integrations. This document synthesizes all six.
**Companion to:** [`2026-06-25T03-40Z-agent-suite-scoping.md`](2026-06-25T03-40Z-agent-suite-scoping.md).

---

## 0. TL;DR — the cross-cutting wins

Every agent's research converged on the **same handful of levers**. Do these once and they pay off across the whole suite:

| Theme | Where it applies | Why it matters (fast / reliable / graph / scale) |
|---|---|---|
| **A. Prompt-cache the stable prefix** (system + tool schema + transcript) | planner, git, meeting-ops, web | ~0.1× input cost + lower TTFT on every call. **⚠️ Gotcha: Opus 4.8 & Haiku 4.5 only cache prefixes ≥ 4096 tokens** — our current ~700-token prompts **silently no-op**. Must *fatten* prefixes (few-shot exemplars, enumerated schema) to engage caching. |
| **B. Intent → templated SQL (skip the LLM on the hot path)** | git, data | The planner already gives us the intent; known shapes (who_changed/blame/recent_changes, the canonical aggregates) become 1 templated `pg_read` (+0–1 phrase call) instead of an agentic SQL-gen loop. Directly serves "fast lookups." `mcp.py` already exposes a programmatic `pg_read()` — no new plumbing. |
| **C. Graph write-back with EDGES + provenance** | meeting-ops (★), research, data, git, web | This is the literal "parse into the graph for fast lookups" goal. Today meeting-ops writes **orphan nodes with no edges** — so nothing is traversable. Every agent should write structured, *linked*, idempotent facts back so future questions are a 1-hop read, not recomputation. |
| **D. Planner cascade: heuristic → Haiku gate → Opus** | planner | THE answer to "a plethora of items." Most utterances aren't requests; today every one hits Opus. A <1ms keyword gate + a cheap Haiku classify removes ~85% of Opus calls (~5× cheaper per demoted utterance). |
| **E. Strict structured outputs** (`strict: true`, closed schemas) | data, meeting-ops, planner-gate | GA on Haiku 4.5 + Opus 4.8, no beta header. Eliminates the malformed-output failure class for tool/SQL/extraction calls. |
| **F. Off-loop heavy work + bounded executors** | data (render), web (Playwright/deploy) | Keep CPU/subprocess work off the shared asyncio loop so the slow path never starves fast tasks under burst. |

**Embeddings are the keystone dependency.** `nodes.embedding` is NULL everywhere → no semantic search, no semantic cache, no hybrid retrieval. Backfilling it (note: **Anthropic has no embeddings API** — use Voyage or OpenAI `text-embedding-3-small`, 1536-dim, via Batch API) unlocks items in git, research, and planner-dedup at once. Aiven Postgres supports `pgvector`/`pg_trgm` natively (no new service).

### Bugs / footguns the research surfaced (fix regardless)
1. **research agent: dynamic filtering is silently OFF.** `web_search_20260209`/`web_fetch_20260209` only filter when the beta header `code-execution-web-tools-2026-02-09` is sent — we send neither it nor anything enabling it, so we pay for the `_20260209` tool but run it as plain search (losing ~24% tokens / ~11% accuracy).
2. **research agent: citation harvest drops web_fetch citations.** It reads `.url` on every citation; web_fetch citations are `char_location` blocks with no `.url` (only `document_index`) — must map index→fetched-URL. This is the "citations didn't attach" symptom.
3. **git agent: `AIVEN_READ_ONLY` defaults to `false`** (`config.py`) — the fast *read* agent can issue writes, and its SQL is LLM-authored over untrusted (transcript/graph) content. Flip to read-only + a dedicated read-only PG role + statement timeout.
4. **meeting-ops writes orphan nodes (no edges).** The agent that's supposed to make the graph queryable doesn't link anything. Highest-value single fix in the suite (Theme C).

---

## 1. git / KG agent (`agents/git.py`) — the fast-path Q&A brain

**Top optimizations (ranked):**
1. **Intent → canned parameterized SQL** for `who_changed`/`blame`/`recent_changes` via the existing `ctx.mcp.pg_read()`, +≤1 Haiku phrase call. Reserve the full `tool_runner` loop for free-form `ask` only. (M)
2. **Prompt-cache schema+tools** — but *fatten* `KG_SYSTEM` (enumerated node/edge types + 2–3 canonical recursive-CTE few-shots) to clear the 4096-token floor; that fattening also improves SQL accuracy. (S)
3. **Read-only enforcement + statement_timeout + LIMIT/single-statement guard** (fixes the write-capability hole; caps tail latency). (S)
4. **Postgres indexes for the access pattern:** `gin(name gin_trgm_ops)` for ILIKE, `(type,lower(name))` btree, `gin(properties jsonb_path_ops)`, `edges(source_node_id,type)` + `edges(target_node_id,type)`, HNSW on embedding once backfilled. (S)
5. **One-shot depth-bounded recursive CTEs** (cycle-safe `UNION`, `WHERE depth < N`); replace "discover types first" with the enumerated vocab to kill a round-trip. (M)
6. **Result cache** keyed `(intent, normalized_args)`, ~60s TTL, invalidated on `kg.updates`. (S)

**Graph-first:** resolve-then-expand (trigram → node id → indexed CTE traversal); write back **materialized `owns` edges** (`person -owns-> code_module` with commit_count/last_touched) and **node summaries** so who_changed/blame become one-hop reads.

**Best integration:** **pgvector HNSW + pg_trgm + recursive CTEs on the existing Aiven Postgres** (zero new infra; use pgvector ≥0.8.0 `iterative_scan=relaxed_order` to fix over-filtering when restricting vectors to a graph-selected candidate set). OpenSearch BM25 only if exact-symbol recall proves insufficient. Apache AGE is **not available on Aiven**; SQL/PGQ (PG19) is beta — defer.

---

## 2. data agent (`agents/data.py`) — quantitative answers as charts

*(Note: the code already offloads rendering via `asyncio.to_thread` + the OO `Figure` API — better than the prompt assumed. The real residual risk is executor saturation + the GIL + matplotlib's global font lock under burst.)*

**Top optimizations (ranked):**
1. **`strict: true` on the `make_chart` tool + a SQL shape-guard:** wrap model SQL as `SELECT label::text, value::float8 FROM (<sql>) t(label,value) LIMIT 50`; on Postgres error, do **one** repair turn feeding the error back. Kills the dominant text-to-SQL failure class. (S)
2. **Rule-based chart-type selection** computed *after* retrieval from `(labels, values)` (n, label length, ordered/temporal, share-of-whole) — model's choice becomes a hint. Fixes "model picks chart before knowing row count." (S)
3. **Content-hash cache** (`sha256(normalized_sql)` → png+summary), bust on `kg.updates`. (S–M)
4. **Dedicated bounded render executor** on `AgentContext` (separate from harness concurrency); ProcessPool if render becomes hot (also makes renders cancellable for `agent.control`). Pre-warm `font_manager`. (M)
5. **Numeric formatting + empty/large-set handling** ("Other" bucket for pies, top-N, "no data" placeholder). (S)

**Graph-first:** templated SQL for the canonical aggregates (skip the planner LLM for the 80% case); write computed aggregates back as **`metric` nodes** (`properties.values`, `computed_at`, `sql_hash`) with `derived_from` edges → repeat asks become an O(1) SELECT.

**Best integration:** **vl-convert (Vega-Lite spec → static PNG)** — replaces brittle matplotlib drawing *and* chart-type plumbing with an LLM-friendly declarative spec rendered by a self-contained Rust binary (no Python global state, no font-lock, parallelizes cleanly). DuckDB/ClickHouse are scale-out paths, not now. Add a deterministic chart-grounding check (assert plotted (label,value) pairs match rows; feed them into `_verify.evidence`).

---

## 3. web agent (`agents/web.py`) — the site builder (slow path)

**Top optimizations (ranked):**
1. **Verify-before-publish:** headless Chromium (Playwright, off-loop via `to_thread`) → catch `pageerror`/truncation, screenshot, only publish if it renders; return the **screenshot as an `image` artifact**. Gate on `final.stop_reason == "max_tokens"` to never publish a half-page. (M)
2. **Real deploy to a public HTTPS URL** keeping site_id-as-stable-URL: Cloudflare Pages Direct Upload (`wrangler pages deploy`, off-loop subprocess) or Vercel `POST /v13/deployments` (inline files, pure HTTP). Fall back to the local served dir when no token present (preserve the no-creds demo path; check key *presence* only). (M)
3. **Patch updates via SEARCH/REPLACE blocks** (exact string match) with full-rewrite fallback — instead of re-emitting the whole file for a one-line change (cuts tokens/latency/truncation risk). (M)
4. **Prompt-cache the system + a design-system scaffold; specificity-rich prompt** (named fonts, base spacing unit, the product palette `#0b1a17`/`#f3ead3`/`#4ade80`, responsive breakpoints) to kill "AI slop." (S)
5. **Dedicated build semaphore** (`asyncio.Semaphore(2)`) around render+deploy only, so heavy builds can't starve fast tasks. (S)

**Graph-first:** pull real KG facts (decisions/action_items) to populate sites ("a status page from today's meeting"), grounded by the verifier; record the built site back as a `website` node (url, brief, revision, screenshot) with `derived_from` edges.

**Best integration:** **Cloudflare Pages Direct Upload** (one command → stable `<site_id>.pages.dev` HTTPS URL, least ops) — turns the demo into a real shareable site. Playwright is the verification backbone. Tailwind v4 browser CDN for demo-grade scaffolds.

---

## 4. meeting-ops agent (`agents/meeting.py`) — ★ the heart of graph-first

**Top optimizations (ranked):**
1. **Create EDGES, not orphan nodes** — the single highest-value change in the suite. After node upsert, a second `aiven_pg_write` inserts edges resolving IDs by join: `decision/action_item -in_meeting-> meeting`, `person -owns-> action_item`, `decision -rationale_for-> topic`, `-mentions-> topic`, `-derived_from-> utterance`. That's what makes "what did we decide about X / who owns Y / what changed since last week" one/two-hop traversals. (M)
2. **Prompt-cache the stable transcript prefix** (it's re-injected on every recap in a meeting; ≥4096 tokens clears the floor; keep meeting_id/timestamps OUT of the cached block). (S)
3. **Strict structured output + grounding fields:** `strict:true`, closed schema, require `evidence_quote` + `speaker` per item → enables a pure-string async `verdict` (quote ∈ transcript) and "never infer owners." (S)
4. **Idempotent dedup keys:** model emits a `slug`; node `name = f"{meeting_id}:{slug}"` so re-runs collapse and cross-meeting items stay distinct (avoids paraphrase-duplication node sprawl). (M)
5. **Entity resolution:** match owner "Bob" → existing `person` node via the meeting's `attended` edge set before creating `owns`; else store `owner_unresolved` and skip the edge (don't invent people). (M)
6. **Two-model split + streaming:** pure short `recap` → Haiku; `action_items`/`decisions`+write-back → Opus (`thinking adaptive`, `effort medium`); stream the recap live. (S)
7. **Bitemporal edges** (`valid_at`/`invalid_at` in edge properties; mark superseded decisions invalid rather than delete) for "what's the *current* decision / what changed since last week." (M, post-hackathon)

**Best integration:** **Linear MCP** (`https://mcp.linear.app/mcp`) — first-party, Anthropic/Cloudflare-built, Bearer-token auth, `create_issue` with assignee/priority/labels. Wire via the MCP connector (`mcp_servers` + `mcp_toolset`, beta `mcp-client-2025-11-20`). This is the change that turns "tells you the action items" into "files assigned tickets" — the real AI-employee beat. Slack (recap delivery) #2; Notion/Google Docs #3.

---

## 5. research agent (`agents/research.py`) — live web (slow path)

**Top optimizations (ranked):**
1. **Graph-first cache check before the web:** `_graph_lookup(question)` (exact-normalized now; pgvector cosine ≥0.93 once embeddings exist) → serve instantly with provenance on hit. Turns the 80–90s slow path into a ms path for repeats (semantic-cache hit rates 30–70% in production). (M)
2. **Turn ON dynamic filtering** (`anthropic-beta: code-execution-web-tools-2026-02-09`), move to `_20260318` tools, add `response_inclusion:"excluded"` + `web_fetch max_content_tokens`. (S) *(ZDR caveat: filtering uses internal code-exec; use `allowed_callers:["direct"]` if ZDR is required.)*
3. **Effort/model router:** simple factual lookup → Sonnet 4.6 + `effort medium` + `max_uses 3`; comparative research → Opus + `high` + `max_uses 18`. Add `user_location`. (M)
4. **Fix citation harvest** (map web_fetch `char_location` → fetched URL). (S)
5. **Transient-error handling:** scan for `*_tool_result_error` blocks (HTTP 200 + error_code), backoff on `too_many_requests`, stop-and-synthesize on `max_uses_exceeded`; wrap the whole run in `asyncio.wait_for`. (S–M)
6. **In-flight dedup** of concurrent identical questions (an `asyncio.Future` per normalized question). (S)

**Graph-first (the key move):** after answering, **write findings back** off the critical path (like the verifier) — a `research_answer` node (question, answer, confidence, retrieved_at, embedding) + one `source_document` node per cited URL + `derived_from`/`relates_to` edges. Then the *git/KG agent* can answer the same question instantly with provenance, no web latency. "Check graph first → else web → write back" is one loop.

**Best integration:** **graph write-back (build, not buy)** is #1 — it's what makes the slow path a reusable asset. Keep correctly-configured Anthropic server tools as the engine (#2); add **Exa Fast** (sub-350ms) as a first-hop to pre-seed URLs for `web_fetch` on the latency-sensitive lane. Avoid Perplexity/Parallel-Pro in the realtime path (11–13s).

---

## 6. planner / router (`planner.py`) — the throughput bottleneck

**Top optimizations (ranked):**
1. **Prompt-cache the stable prefix** (system + tool schema, identical every utterance). ⚠️ ~700 tokens is below the 4096 floor → no-op until fattened (couples with #4). (S)
2. **Haiku fast-classify cascade:** a cheap `model_fast` "is this plausibly actionable?" gate; only escalate to Opus `propose_tasks` on pass/uncertain. Removes ~85% of Opus calls (~5× cheaper each). `model_fast` is already configured but unused. (M)
3. **<1ms keyword/imperative pre-filter** before any LLM (imperative verbs, "?", "can you…"); high-recall, never the final arbiter. (S)
4. **Route on rolling context + few-shot exemplars** (last 1–3 utterances) to catch split-across-turns requests — and the exemplars grow the prefix past the cache floor. (S–M)
5. **Confidence bands** (>0.8 decide, 0.5–0.8 escalate to Opus, <0.5 drop) — **fail open toward Opus, never silent-drop** (a missed request is a broken promise). (S)
6. **Semantic dedup** of the same ask said twice (per-meeting `(intent, norm_args)` TTL window). (M)

**Scale strategy:** a 3-tier funnel — heuristic (<1ms) → Haiku gate (cached) → Opus plan (cached) — so the bulk of traffic dies cheaply before the expensive stage. Micro-batch/window finals (1–2s) so multi-turn requests route as a unit; keep cheap stages synchronous, only the Opus stage concurrent.

**Best technique:** the **Haiku cascade (#2)** is the single biggest cost/throughput win; **prompt caching (#1)** is strictly easier and should ship alongside (it makes both stages cheap).

---

## 7. Prioritized cross-agent roadmap

Ordered by impact-per-effort, grouped so shared work is done once:

**Tier 1 — cheap, high-impact, ship first**
1. **Planner: keyword pre-filter + Haiku cascade gate** (Theme D) — biggest cost/throughput win; `model_fast` already wired. *(planner.py)*
2. **Prompt caching everywhere, with fattened prefixes** to clear the 4096 floor (Theme A) — planner, git, meeting-ops, web. Verify `cache_read_input_tokens > 0`.
3. **meeting-ops: write EDGES + idempotent slug keys + grounding fields** (Theme C ★) — makes the graph actually queryable. *(meeting.py)*
4. **git: flip `AIVEN_READ_ONLY=true` + statement_timeout + single-SELECT guard** (safety/latency). *(config/env + git.py)*
5. **data: `strict:true` + SQL shape-guard + one repair turn** (Theme E). *(data.py)*
6. **research: enable dynamic filtering beta header + fix citation harvest + run timeout** (bug fixes). *(research.py)*

**Tier 2 — medium effort, high value**
7. **Postgres index migration** (trgm/jsonb/edge/btree) + **embedding backfill** (Voyage/OpenAI Batch) — unlocks git hybrid retrieval, research semantic cache, planner semantic dedup.
8. **Intent→templated SQL fast paths** for git (who_changed/blame/recent_changes) and data (canonical aggregates) (Theme B).
9. **research: graph write-back + graph-first cache check** (Theme C) — turn the slow path into a reusable KG asset.
10. **data: dedicated render executor + content-hash cache + vl-convert** (Theme F + integration).
11. **web: verify-before-publish (Playwright) + real deploy (Cloudflare Pages) + patch updates** (reliability + the shareable-URL win).

**Tier 3 — the "AI employee" + durability layer**
12. **meeting-ops → Linear MCP** (file assigned tickets) + Slack recap delivery — the standout integration.
13. **meeting-ops bitemporal edges** ("current decision / what changed since last week").
14. **git/data: materialized `owns` edges + `metric` nodes**; result caches with `kg.updates` invalidation.

---

## 8. Consolidated sources (deduped highlights)

- Anthropic: [prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [structured outputs / strict tools](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [web search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool), [web fetch](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool), [server tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/server-tools)
- Postgres/retrieval: [pgvector](https://github.com/pgvector/pgvector) + [0.8.0 filtered search](https://www.postgresql.org/about/news/pgvector-080-released-2952/), [recursive CTEs](https://www.postgresql.org/docs/current/queries-with.html), [Aiven extensions (pgvector/pg_trgm; AGE absent)](https://aiven.io/docs/products/postgresql/reference/list-of-extensions), [pganalyze GIN](https://pganalyze.com/blog/gin-index), [OpenSearch RRF](https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/)
- Text-to-SQL: [AIMultiple 2026](https://research.aimultiple.com/text-to-sql/), [K2view](https://www.k2view.com/blog/llm-text-to-sql/), [intent→SQL templates](https://medium.com/data-science-collective/intent-driven-natural-language-interface-a-hybrid-llm-intent-classification-approach-e1d96ad6f35d), [OWASP SQLi](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html)
- Rendering/charts: [vl-convert](https://github.com/vega/vl-convert), [Altair saving](https://altair-viz.github.io/user_guide/saving_charts.html), [DuckDB vs pandas](https://www.codecentric.de/en/knowledge-hub/blog/duckdb-vs-dataframe-libraries), [matplotlib free-threading #28611](https://github.com/matplotlib/matplotlib/issues/28611), [don't block the event loop](https://towardsdatascience.com/async-for-data-scientists-dont-block-the-event-loop-ab245e28ee01/)
- Web build/deploy: [Cloudflare Pages Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/), [Vercel create deployment](https://vercel.com/docs/rest-api/deployments/create-a-new-deployment), [Playwright screenshots](https://playwright.dev/python/docs/screenshots), [SEARCH/REPLACE edit formats](https://www.morphllm.com/edit-formats), [To Diff or Not to Diff](https://arxiv.org/html/2604.27296), [avoid AI slop](https://www.mindstudio.ai/blog/claude-design-avoid-generic-ai-aesthetics)
- Meeting-ops/KG: [Zep/Graphiti temporal KG (arXiv 2501.13956)](https://arxiv.org/abs/2501.13956), [LINK-KG coref dedup (arXiv 2510.26486)](https://arxiv.org/abs/2510.26486), [Galileo summarization](https://galileo.ai/blog/llm-summarization-strategies), [NexusSum (arXiv 2505.24575)](https://arxiv.org/html/2505.24575v1), [Linear MCP](https://linear.app/docs/mcp)
- Research/search: [Exa](https://exa.ai/docs/reference/search-api-guide), [search latency benchmark](https://findskill.ai/blog/web-infrastructure-for-ai-agents-parallel-vs-exa-tavily-brave/), [deep-research survey (arXiv 2508.12752)](https://arxiv.org/html/2508.12752v1), [semantic caching](https://www.respan.ai/articles/semantic-cache-llm)
- Routing/cascades: [LLM routing & cascades](https://tianpan.co/blog/2025-11-03-llm-routing-model-cascades), [early-abstention cascades (arXiv 2502.09054)](https://arxiv.org/abs/2502.09054), [intent detection w/ LLMs (arXiv 2410.01627)](https://arxiv.org/abs/2410.01627), [stream dedup](https://streamkap.com/resources-and-guides/data-deduplication-streaming)

*(Full per-agent source lists and code-level diffs are in the six subagent reports; this document is the synthesis.)*

---

## 9. Implementation status (Tier-1 landed via per-agent subagents)

A second round of six per-agent subagents implemented the safe, self-contained Tier-1 wins (one file each; shared files left untouched; each compile + import + isolation-tested; KG test rows cleaned up). All six compile, import, and the registry resolves every intent.

| Agent | Implemented | Verified | Deferred |
|---|---|---|---|
| **meeting.py** | ✅ writes **edges** now (meeting node + `in_meeting` + person nodes + `owns`), idempotent `{meeting_id}::{slug}` names, strict schema + `evidence_quote`/`speaker` grounding fields | recap test → 4 nodes + 3 edges, then cleaned | bitemporal valid/invalid edges, topic/rationale_for edges, transcript prompt-cache, Linear MCP |
| **git.py** | ✅ canned templated-SQL fast path for `who_changed`/`blame`/`recent_changes` (+ TTL result cache) with **graceful fallback to the LLM loop** | `recent_changes` ran canned (real commit data); `who_changed` w/o module correctly fell back; `ask` LLM path intact | prompt-cache/fatten schema, indexes, embedding backfill, hybrid search, materialized `owns` |
| **data.py** | ✅ `strict` tool + closed schema, **SQL shape-guard + one repair turn**, rule-based chart selection, content-hash cache, dedicated bounded render executor | chart rendered (52 KB), 2 runs clean | vl-convert/Vega-Lite, DuckDB, `metric` write-back, ProcessPool |
| **web.py** | ✅ anti-slop design contract (Fraunces+Sora, product palette), **SEARCH/REPLACE patch updates** w/ full-rewrite fallback, truncation guard | build OK; update used patch path surgically (2-byte H1 delta) | Playwright verify+screenshot, real Cloudflare/Vercel deploy, prompt-cache, KG read/write-back |
| **research.py** | ✅ citation harvest fix **+ fallback to actually-searched/fetched URLs** (sources were empty before), 120s whole-run timeout, transient web-tool-error resilience | live run ~16s, **10 real sources** populated | graph write-back/cache, model/effort routing, Exa/Tavily. (Beta header for dynamic filtering **confirmed NOT needed** — `_20260209` already filters.) |
| **planner.py** | ✅ zero-LLM keyword pre-filter, **Haiku fast-classify gate** before Opus (fail-open), prompt-cache + few-shot exemplars | live: "yeah totally"→skip, compound→2 tasks, "yeah sounds good"→0, gate fail-opens to Opus | semantic dedup, micro-batching, embedding gate, confidence-band tuning |

**Cross-cutting caveats to remember:**
- **Prompt caching only engages at ≥4096-token prefixes** (Opus 4.8 / Haiku 4.5). The planner's few-shot exemplars push toward that; verify with `usage.cache_read_input_tokens > 0` and fatten further if it reads 0. Other agents' inline caching is wired but may be under the floor until prompts grow.
- **`AIVEN_READ_ONLY` was deliberately NOT flipped** — it's a single global MCP setting; turning it on would break meeting-ops/research/data write-backs. A read-only role for the git agent is the proper (deferred) fix.
- **data cache is per-process** (hits in the long-lived runner, not across CLI runs).
- **research `answer` = last text block** can be a fragment when synthesis spans blocks (pre-existing; flagged, not yet fixed).
- The git canned `who_changed` path **requires a module/file term**; without one it (correctly) falls back to the LLM path.

Two partial-edit bugs from a rate-limited first attempt were caught and fixed during integration: meeting's `run()` not unpacking the new `(nodes, edges)` tuple, and git's `run()` not routing to the new `_canned()` (it was dead code). Both fixed + tested.

— Researched + implemented by per-agent subagents (Opus 4.8), integrated/verified by Claude
