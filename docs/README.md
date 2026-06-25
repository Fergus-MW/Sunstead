# docs/ — how this documentation works

Sunstead's docs are split by **tense**, so any agent (or human) knows where to look:

| Doc | Tense | What it is | Edit rule |
|---|---|---|---|
| **[OVERVIEW.md](OVERVIEW.md)** | **now** | The HEAD. Current state of the repo: what exists, what's wired, what isn't, where the code lives. A new agent's entrypoint. | **Kept current** — rewrite sections so they always describe reality. |
| **[DESIGN.md](DESIGN.md)** | **future** | The target architecture + the plan to get there. The single forward source-of-truth; used to *sync the fragmented parts* into one system. | **Edit when direction changes.** Supersedes scattered planning. |
| **[LOG.md](LOG.md)** | **past** | The history of decisions — intent, analysis, and *why*, signed by the agent that did the work. More insightful than `git log`. | **Append-only.** Never edit or delete a past entry. |

**Read order for a new agent:** OVERVIEW (where are we) → DESIGN (where are we going) → skim the last few LOG entries (how we got here / why).

**Operator runbook:** [DEPLOY.md](DEPLOY.md) — one-command local stack + per-component deploy (how to run/ship, not why).

**Deep references** (detailed, being consolidated into DESIGN over time): [PLAN.md](PLAN.md) (whole-system),
[AGENT_SYSTEM.md](AGENT_SYSTEM.md) (the agent suite), [HACKINFO.md](HACKINFO.md) (challenge + rubric),
[CENTRAL-KG-API.md](CENTRAL-KG-API.md) (the KG service). When DESIGN and a reference disagree, **DESIGN wins** and the reference should be folded in.

---

## The LOG protocol

The point of LOG.md is to capture what `git` can't: **intent, the options considered, why we chose what we chose, and what we learned.** A future agent should be able to read it and understand the *reasoning* behind the repo, not just the diffs.

**Write a LOG entry when you:**
- make or change an architectural decision (transport, deployment, a seam, a contract),
- finish a meaningful build or wire-up,
- discover something that changes the plan (a teammate's branch, a constraint, a measurement),
- merge work to `main`.

Don't log trivial edits. Do log the *why* behind non-trivial ones.

**Entry template** (newest entries at the **top** of LOG.md, under the header):

```markdown
## YYYY-MM-DD — <short title>

**What:** one or two sentences on the change/decision/finding.
**Why:** the reasoning — what problem it solves, what alternatives were weighed.
**Analysis / consequences:** what this unlocks or constrains; risks; what's now true that wasn't.
**Touches:** files / branches / docs affected.

— <Agent name (model)>, signed off
```

**Sign-off** is by the agent that did the work (the human is the operator, the agent is the builder). Keep entries tight but insightful — a paragraph of real reasoning beats a page of narration.
