# Infra

Everything you need to stand up the Aiven side of Sunstead.

```
infra/
├── provision.sh         # idempotent: creates the Aiven services if they don't exist
├── aiven-mcp.json       # config template for the Aiven MCP server
└── README.md            # this file
```

The repo deliberately **does not** commit any connection strings or passwords.
Provisioning is `avn`-CLI-driven and writes secrets to a local `.env` only
(see `central-kg-api/.env.example`).

---

## Live services (as of the hack — `avn project: jq01`)

| Service | Plan | Cloud | Why |
|---|---|---|---|
| `central-kg-pg` | `pg:free-1-1gb` (PG 17) | `do-lon` | Graph + pgvector + recursive-CTE traversal |
| `central-kg-os` | `opensearch:free-4-20` (OS 3.3) | `do-lon` | BM25 / k-NN entry point over node text |
| ~~`central-kg-kafka`~~ | — | — | Not provisioned yet; needs a paid plan (no Kafka free tier) |

Both free-tier services run in `do-lon` because Aiven's free tier is only
available on `DigitalOcean` / `UpCloud` (not AWS). When we move the listener
agent / Lambda to production, both this and the AWS Lambda region should
co-locate (`aws-eu-west-2` is the same metro) — that's worth ~80ms per query.

---

## Provisioning

```bash
brew install aiven-client
avn user login you@example.com --token

./infra/provision.sh
```

The script is **idempotent** — running it twice doesn't re-create anything. It
only calls `avn service create` for services that don't already exist.

After provisioning, set passwords (Aiven's API redacts stored passwords by
default; the only way to get a usable credential is to reset it):

```bash
avn service user-password-reset --project jq01 \
    --username avnadmin --new-password "$(openssl rand -base64 24)" central-kg-pg
avn service user-password-reset --project jq01 \
    --username avnadmin --new-password "$(openssl rand -base64 24)" central-kg-os
```

Then apply the schema and write the URIs into `central-kg-api/.env`:

```bash
psql "$PG_URI" -f central-kg-api/schema.sql
```

---

## Aiven MCP server

This is the lever for the "Aiven MCP" challenge — agents talk to the data
plane through this server. See `aiven-mcp.json` for the config template.

### Install (user scope, machine-wide)

```bash
# 1. Get an API token
avn user access-token create --description "claude-code-mcp" \
    --max-age-seconds 7776000
# Copy the FULL_TOKEN value somewhere safe.

# 2. Register the MCP server in Claude Code
claude mcp add --scope user aiven-mcp \
    -e "AIVEN_TOKEN=<paste full token>" \
    -e "AIVEN_READ_ONLY=false" \
    -e "AIVEN_ALLOW_SECRETS=true" \
    -- npx -y mcp-aiven

# 3. Verify
claude mcp list | grep aiven-mcp
# expect: aiven-mcp: npx -y mcp-aiven - ✓ Connected
```

### What this MCP exposes (54 tools as of `mcp-aiven 1.11.2`)

Highlights:

- **`aiven_pg_read`** / **`aiven_pg_write`** — direct SQL against any Aiven PG service.
  This is the canonical agent → knowledge-graph read path per `docs/PLAN.md §3.5`.
- **`aiven_kafka_topic_create`** / **`_message_produce`** / **`_message_list`** —
  agent-to-agent communication on Kafka.
- **`aiven_service_create`** / **`_get`** / **`_list`** — provision new services
  via MCP (do this on camera for judges).
- **`aiven_service_connection_info`** — fetch live credentials (only because
  we set `AIVEN_ALLOW_SECRETS=true` for dev convenience; for prod, unset it).
- **`aiven_pg_optimize_query`** — EverSQL-style query optimisation.
- **`aiven_kafka_connect_*`** — Kafka Connect surface, useful for the real
  Postgres → Kafka → OpenSearch CDC pipeline if we want to ship that.

There is no `aiven_opensearch_search` tool. For OpenSearch you call the
service's HTTP endpoint directly (it's still hosted on Aiven — the MCP just
hasn't exposed a search abstraction). The agent can use `aiven_service_connection_info`
to retrieve the URL once, then issue normal OpenSearch HTTP queries.

### Security notes

- The MCP `AIVEN_TOKEN` is stored as a plaintext env var in `~/.claude.json`
  (or `.mcp.json` if you use project scope). It's a personal access token —
  it inherits your Aiven permissions. Treat it like an SSH key.
- Revoke leaked tokens with `avn user access-token revoke <token-prefix>`.
- For production, prefer service-account tokens with scoped IAM rather than
  a personal token.
