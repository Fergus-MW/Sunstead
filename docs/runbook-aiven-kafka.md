# Runbook — point the agent suite at the live Aiven Kafka

Goal: route the dispatch (gateway → `agent.tasks.*` → runner → results) through **Aiven Kafka
`kafka-254bd14f`** instead of local redpanda — the strongest "swarm via Aiven Kafka pub/sub" evidence.
(Our topics already live there, created via MCP.)

## What's already done (automated)

`uv run python scripts/fetch_kafka_creds.py` (uses the `avn` CLI; token passed in, never printed):
- ✅ downloaded **`ca.pem`** + **`service.cert`** into `agent-system/` (gitignored)
- ✅ found the bootstrap: **`kafka-254bd14f-jq01.h.aivencloud.com:28095`**, cluster RF **3**

## The one operator step (30 seconds)

The **private key is redacted for the API token** (`avn` and MCP both return `<redacted>` — the token isn't
scoped to expose private creds; this is correct security). Only an authenticated admin in the browser can get it:

> **Aiven Console → `kafka-254bd14f` → Connection information → Authentication: _Client certificate_ →
> download _Access Key_ → save it as `agent-system/service.key`.**

(Alternative: re-run `fetch_kafka_creds.py` after putting a **full-access personal token** in `.env` as
`AIVEN_TOKEN` — then it downloads all three files itself.)

## Wire + test

Add to `agent-system/.env`:
```
KAFKA_BOOTSTRAP=kafka-254bd14f-jq01.h.aivencloud.com:28095
KAFKA_SECURITY=SSL
KAFKA_CA_PATH=./ca.pem
KAFKA_CERT_PATH=./service.cert
KAFKA_KEY_PATH=./service.key
KAFKA_RF=3
```
Then:
```bash
make smoke      # produce+consume round-trip — now over Aiven Kafka (proves the connection)
make run        # agent-runner consumes agent.tasks.* from Aiven Kafka
make gateway    # POST /tasks → Aiven Kafka → runner → results
```
`shared/kafka.py` already speaks mTLS (`KAFKA_SECURITY=SSL`), so this is config-only — no code change.

## Why not SASL?

SASL would be one cert file fewer, but the **SASL password is redacted for the token too** — so it's still an
operator step, and `kafka-254bd14f` is mTLS-only today (enabling SASL is an extra service change). With `ca.pem` +
`service.cert` already fetched, mTLS needs just the **one** key file — fewer clicks. If you prefer SASL later:
enable it (Console / `avn service update -c kafka_authentication_methods.sasl=true`), grab the `avnadmin` password
from the Console, set `KAFKA_SECURITY=SASL_SSL` + `KAFKA_USERNAME=avnadmin` + `KAFKA_PASSWORD=…` + `KAFKA_CA_PATH`.

## Fallback

If you'd rather not touch creds before the pitch: the dispatch demo runs on local redpanda (`make up`), and
"topics created on Aiven Kafka via MCP" already stands as the Aiven-Kafka + autonomy evidence.
