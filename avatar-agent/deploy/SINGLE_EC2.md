# Deploying avatar-agent on a single EC2 box

The full stack in [README.md](README.md) spreads across four AWS services
(EC2 + Fargate + S3/CloudFront + Lambda). For Sunstead we run the **whole
realtime layer on one EC2 instance** instead — the avatar is its own host,
joined to the rest of the system only by two outbound HTTP edges. This is the
deployment the [docs/DESIGN.md](../../docs/DESIGN.md) topology calls for
("avatar-agent: LiveKit on EC2").

## What collapses, and why it's safe

| Full stack | Single-EC2 | Why it still works |
|---|---|---|
| LiveKit on EC2 | **same** | WebRTC needs a wide UDP range + a stable public IP — keep the VM. |
| Agent worker on **Fargate** | **a container on the box** | The box already runs docker-compose; add one service. The worker connects to LiveKit over `ws://localhost:7880` (no public hop). |
| Viewer on **S3 + CloudFront** | **Caddy serves `viewer/`** | Caddy already terminates TLS on the box → it can serve the static viewer over the same HTTPS origin (Recall needs a secure context for `getUserMedia`/`wss`). |
| Dispatch on **Lambda** | **`dispatch-bot` CLI, or a tiny route** | The join logic is one function (`dispatch_bot`); run it from the box for the demo, or expose it as a small HTTP route later. |
| Secrets in **SSM** | **`/opt/avatar/.env` on the box** (or SSM via instance role) | One host, one env file readable only by root. |

The two edges that leave the box (unchanged from the multi-host design):
- `GATEWAY_URL` → the **agent-runner gateway** (delegation; `delegate` tool → `POST /tasks`).
- `BACKEND_URL` → **central-kg-api** (the avatar's KG reads).

Both are plain outbound HTTPS — the avatar EC2 needs **no inbound** from the rest
of Sunstead, only the LiveKit media/signaling ports for Recall + participants.

## The consolidated docker-compose

Extends the LiveKit compose that `livekit_user_data.sh.tftpl` already lays down
in `/opt/livekit`. Three services, all `network_mode: host` so the worker reaches
LiveKit on localhost and Caddy owns 80/443:

```yaml
# /opt/avatar/docker-compose.yaml
services:
  caddy:
    image: caddy:2
    network_mode: host
    restart: unless-stopped
    volumes:
      - /opt/avatar/Caddyfile:/etc/caddy/Caddyfile
      - /opt/avatar/viewer:/srv/viewer:ro      # the static viewer page
      - /opt/avatar/caddy_data:/data

  livekit:
    image: livekit/livekit-server:latest
    command: --config /etc/livekit.yaml
    network_mode: host
    restart: unless-stopped
    volumes:
      - /opt/avatar/livekit.yaml:/etc/livekit.yaml

  avatar-worker:
    image: <ECR_OR_LOCAL>/avatar-agent:latest    # built from avatar-agent/Dockerfile
    command: avatar-agent start
    network_mode: host
    restart: unless-stopped
    env_file: /opt/avatar/.env
    # worker talks to the local LiveKit; everything else is egress
```

`livekit.yaml` and the LiveKit `Caddyfile` block are exactly what the existing
templates render (`templates/livekit.yaml.tftpl`, `templates/Caddyfile.tftpl`).

## Caddyfile — add a viewer route

Serve the viewer from the same TLS origin LiveKit already uses (or a second
domain). Appended to the rendered Caddyfile:

```caddyfile
# wss for LiveKit signaling (existing)
livekit.sunstead.example.com {
	reverse_proxy localhost:7880
}

# the avatar viewer page Recall loads as the bot's camera (new)
viewer.sunstead.example.com {
	root * /srv/viewer
	file_server
}
```

Then `VIEWER_URL=https://viewer.sunstead.example.com/` in the worker `.env`.
Point both A records at the instance's Elastic IP.

## The worker `.env` on the box

```bash
# /opt/avatar/.env  (root-only; or hydrate from SSM at boot via instance role)
# ── LiveKit: the worker dials the LOCAL server; viewer/dispatch use the public domain ──
LIVEKIT_URL=ws://localhost:7880
LIVEKIT_API_KEY=<same key baked into livekit.yaml>
LIVEKIT_API_SECRET=<same secret>
VIEWER_URL=https://viewer.sunstead.example.com/

# ── Recall + Anam + cognition ──
RECALL_API_KEY=...
ANAM_API_KEY=...
ANAM_AVATAR_ID=...
PIPELINE_MODE=cascade
ANTHROPIC_API_KEY=...
DEEPGRAM_API_KEY=...
CARTESIA_API_KEY=...

# ── The two seams out to the rest of Sunstead ──
BACKEND_URL=https://central-kg-api.example.com     # KG reads
GATEWAY_URL=https://gateway.example.com            # delegate() → agent.tasks.*
```

> ⚠️ `dispatch.py` mints the **viewer** token, so the *dispatcher* must use a
> `LIVEKIT_URL` the browser can reach (the public `wss://`), while the **worker**
> uses `ws://localhost:7880`. Keep two values if you run dispatch on the box: set
> the dispatcher's `LIVEKIT_URL` to `wss://livekit.sunstead.example.com`. (The
> token's key/secret are shared; only the URL differs.)

## Send the bot in

```bash
# on the box, in the avatar-agent venv / container:
dispatch-bot https://meet.google.com/abc-defg-hij
# → prints bot_id, room, meeting_id, viewer_url
```

The printed `meeting_id` is the canonical key the FE subscribes to
(`WS /stream?meeting_id=<id>`), so a `delegate()`'d result correlates to this
call on the dashboard.

## If you want it in Terraform

Minimal change to the existing stack rather than a rewrite:

- **Keep**: `network.tf`, `livekit.tf` (the EC2 + EIP + SG), `versions.tf`.
- **Drop / comment**: `agent.tf` (Fargate), `viewer.tf` (S3/CloudFront),
  `dispatch.tf` (Lambda), and the `ecr.tf` repos you no longer push to.
- **Edit `livekit_user_data.sh.tftpl`** to render `/opt/avatar/docker-compose.yaml`
  with the three services above (it already renders the LiveKit two), drop the
  worker `.env` (from SSM via the instance profile, or `terraform`-templated),
  and `docker compose up -d`.
- **Open the viewer ports**: the LiveKit SG already allows 80/443 for Caddy's
  ACME + signaling, so the viewer route needs no new ingress.
- **Give the instance an IAM role** with `ssm:GetParameter` if you keep secrets
  in SSM instead of a baked `.env`.

This keeps one box, one compose file, one TLS endpoint — and the avatar reaches
the rest of Sunstead purely as an HTTP client, exactly as the design intends.
```
