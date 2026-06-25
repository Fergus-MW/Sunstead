# Deploy the agent suite — AWS EC2 + Aiven Kafka + Cloudflare Tunnel

> Operator runbook for the **agent suite only** (`runner` + `planner` + `gateway` + `sites`). The agents reach
> data via Aiven MCP, so this box does **not** need `central-kg-api` (that's separate — Lambda or its own
> container; only the FE's `/graph` needs it). General deploy map: [DEPLOY.md](DEPLOY.md). Architecture: [DESIGN.md](DESIGN.md) §4.

**Shape (decided):** one always-on **EC2** box runs the four processes from one image via
`docker-compose.aiven.yml`; the bus is **Aiven Kafka** (`kafka-254bd14f`, mTLS); a **Cloudflare Tunnel** fronts the
gateway + sites with public TLS and **zero inbound ports**.

```
 Vercel FE ──wss/https──▶ gateway.<domain> ─┐
 browser   ──https─────▶ sites.<domain>   ──┤ Cloudflare Tunnel (outbound only)
                                            ▼
   EC2 (t3.medium) ── docker compose: runner · planner · gateway:8800 · sites:8810 · cloudflared
                                            │ mTLS (SSL)
                                            ▼
                              Aiven Kafka  kafka-254bd14f
   agents ── Aiven MCP (mcp-aiven, in-image) ──▶ Aiven Postgres + pgvector
```

---

## Fast path (scripted)

Three artifacts in `agent-system/deploy/` cut the manual work to a checklist:
- **`deploy/cloudformation.yml`** — launches the EC2 + an SSH-only security group and installs Docker + clones the
  repo on first boot. One stack, two params (your key pair + your IP).
- **`.env.aws.example`** — the deploy env with the Aiven Kafka values prefilled; you fill **4** secrets.
- **`deploy/deploy.sh`** — preflights the certs/secrets, builds, starts the Aiven+Cloudflare stack, waits for health.

With the scripts your part is: **§0 (gather)** → deploy the CFN stack → SSH in → drop in the 3 certs + `.env` →
`bash deploy/deploy.sh` → map the Cloudflare hostnames → point Vercel at them. The detailed steps below explain each.

---

## 0. Gather first (the operator-only bits)

| Need | Where | Note |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com | the agents' LLM |
| `AIVEN_TOKEN` | console.aiven.io/profile/tokens | for `mcp-aiven`; org "Allow MCP connection" must be on |
| **Aiven Kafka certs** | Aiven Console → `kafka-254bd14f` → Connection information → **Client certificate** | download **`service.key`** (the one piece no token can fetch — it's redacted), plus `ca.pem` (CA) + `service.cert` (Access cert) |
| Cloudflare domain + **Tunnel token** | Cloudflare Zero Trust → Networks → Tunnels | a domain on Cloudflare; create a tunnel, copy its token |
| AWS account | — | to launch one EC2 instance |

Bootstrap is **`kafka-254bd14f-jq01.h.aivencloud.com:28095`**, RF **3** (already known).

---

## 1. Launch the EC2 instance

- **Type:** `t3.medium` (4 GB — four Python procs + Node `mcp-aiven` + matplotlib are tight on 2 GB).
- **AMI:** Ubuntu 22.04 LTS (or Amazon Linux 2023). **Disk:** 20 GB gp3.
- **Security group:** inbound **SSH (22) from your IP only**. **No 8800/8810** — the tunnel dials out, so nothing else needs to be open. Egress: allow all.
- Add your SSH key; launch; note the public IP.

## 2. Bootstrap the box (Docker + Compose + git)

```bash
ssh ubuntu@<public-ip>
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
# Docker Engine + compose plugin (official convenience script)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker      # run docker without sudo
docker compose version                               # sanity
```

## 3. Code + secrets onto the box

```bash
git clone https://github.com/Fergus-MW/Sunstead.git
cd Sunstead/agent-system
```
Place the three Aiven cert files **next to the compose** (`Sunstead/agent-system/`): `ca.pem`, `service.cert`,
`service.key` (scp them up, or paste — they're gitignored, never commit them).

Create `agent-system/.env`:
```bash
# --- Anthropic ---
ANTHROPIC_API_KEY=sk-ant-...
MODEL_SMART=claude-opus-4-8
MODEL_FAST=claude-haiku-4-5

# --- Aiven MCP ---
AIVEN_TOKEN=...
AIVEN_SERVICES_SCOPE=pg,kafka

# --- Aiven Kafka (mTLS; cert paths are the in-container mount points) ---
KAFKA_BOOTSTRAP=kafka-254bd14f-jq01.h.aivencloud.com:28095
KAFKA_SECURITY=SSL
KAFKA_CA_PATH=/app/ca.pem
KAFKA_CERT_PATH=/app/service.cert
KAFKA_KEY_PATH=/app/service.key
KAFKA_PARTITIONS=3
KAFKA_RF=3

# --- Web-agent artifacts (served via the sites tunnel hostname) ---
SITES_BASE_URL=https://sites.<your-domain>

# --- Cloudflare Tunnel ---
CLOUDFLARE_TUNNEL_TOKEN=...
```
> Aiven `kafka-254bd14f` uses **certificate auth** → `KAFKA_SECURITY=SSL` (mTLS). Do **not** use `SASL_SSL` even if a
> username/password exists — the connection silently closes (see `docker-compose.aiven.yml` notes).

## 4. Cloudflare Tunnel hostnames

In the Zero Trust dashboard, on your tunnel, add two **Public Hostnames**:
- `gateway.<your-domain>` → `HTTP` → `http://gateway:8800`
- `sites.<your-domain>` → `HTTP` → `http://sites:8810`

(Setup details are in [`agent-system/docker-compose.cloudflared.yml`](../agent-system/docker-compose.cloudflared.yml).)

## 5. Launch

```bash
cd Sunstead/agent-system
docker compose -f docker-compose.aiven.yml -f docker-compose.cloudflared.yml up --build -d
docker compose -f docker-compose.aiven.yml -f docker-compose.cloudflared.yml logs -f topics runner
```
The `topics` one-shot creates/verifies the topics on Aiven (idempotent), then `runner`/`planner`/`gateway`/`sites`/
`cloudflared` come up. First build is a few minutes (`uv sync` + `mcp-aiven`).

## 6. Point the FE (Vercel) at it

Set in the `meet-joiner` Vercel project, then redeploy:
```
NEXT_PUBLIC_GATEWAY_WS_URL = wss://gateway.<your-domain>/stream
GATEWAY_URL                = https://gateway.<your-domain>
KG_API_URL                 = https://<central-kg-api host>     # only for /graph
```

## 7. Verify

```bash
curl https://gateway.<your-domain>/health                      # {"ok":true}
# fire a task end-to-end through Aiven Kafka:
curl -X POST https://gateway.<your-domain>/tasks \
  -H 'content-type: application/json' \
  -d '{"intent":"ask","args":{"question":"who attended the standups?"},"meeting_id":"mtg_dev"}'
```
Then open the dashboard (FE `/dashboard`) — the activity + result stream over the gateway WS.

## 8. Operate

- **Logs:** `docker compose -f docker-compose.aiven.yml -f docker-compose.cloudflared.yml logs -f planner runner`
- **Update:** `git pull && docker compose -f docker-compose.aiven.yml -f docker-compose.cloudflared.yml up --build -d`
- **Stop:** `... down` (add `-v` to drop the sites/sessions volumes).

## Notes

- **Cost:** a `t3.medium` is ~$30/mo on-demand; **stop the instance when idle** (data is on Aiven, not the box).
- **Security:** no inbound app ports (tunnel is outbound); secrets live in `.env` + the cert files on the box, never
  in the image (`.dockerignore` keeps them out of the build).
- **The `service.key` gate:** it's the only credential no API token can fetch (Aiven redacts the private key) — it
  must come from the Console by a human admin. `ca.pem` + `service.cert` can be re-fetched with
  `scripts/fetch_kafka_creds.py` if needed (see [runbook-aiven-kafka.md](runbook-aiven-kafka.md)).
- **Sites alternative:** set `VERCEL_TOKEN` + `VERCEL_PROJECT` in `.env` to publish web-agent sites to Vercel instead;
  then `sites`/`:8810` and its tunnel hostname aren't needed.
