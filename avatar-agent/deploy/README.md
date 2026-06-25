# Deploying avatar-agent on AWS

Terraform stack that runs the whole avatar agent on AWS:

| Piece | AWS service | Why |
|---|---|---|
| **LiveKit SFU** (self-hosted) | EC2 + Elastic IP + Caddy auto-TLS | WebRTC needs a wide UDP port range + a stable public IP → a VM, not Fargate. |
| **Agent worker** | ECS **Fargate** service | Always-on, egress-only, horizontally scalable. Runs `avatar-agent start`. |
| **Viewer page** | S3 + CloudFront | Static, public **HTTPS** (Recall needs a secure context for `getUserMedia`/`wss`). |
| **Dispatch** | Lambda (container) + Function URL | `meet-joiner` POSTs a Meet link to send the bot in — no creds in the browser. |
| **Secrets** | SSM Parameter Store (SecureString) | Injected into ECS natively; fetched by the Lambda at cold start. |

```
                 ┌───────────────────── AWS ─────────────────────┐
   meet-joiner ─▶│  Lambda (dispatch) ──┐                         │
   (Vercel)      │                      │ creates Recall bot      │
                 │  Fargate: agent worker ──wss──▶ EC2: LiveKit    │
                 │  S3+CloudFront: viewer                          │
                 │  SSM: secrets                                   │
                 └───────────────────┬────────────────────────────┘
   Recall bot ◀── output_media ──────┘  (loads viewer over HTTPS, joins LiveKit room)
```

> **LiveKit needs a domain.** Self-hosted TLS means two DNS records pointing at
> the EIP this stack outputs. Have a domain you can edit ready.

## Prerequisites

- Terraform ≥ 1.5, AWS CLI, Docker — all authenticated to the target account/region.
- A domain you control (for `livekit_domain` + `livekit_turn_domain`).
- Provider API keys: Recall, Anam, and your cognition pipeline (Anthropic +
  Deepgram + Cartesia for `cascade`, or OpenAI for `realtime`).

## 1. Configure

```bash
cd deploy/terraform
cat > terraform.tfvars <<'EOF'
region              = "us-east-1"
livekit_domain      = "livekit.sunstead.example.com"
livekit_turn_domain = "turn.sunstead.example.com"
acme_email          = "fergus@60x.ai"
backend_url         = "https://central-kg-api.example.com"
# pipeline_mode     = "cascade"   # or "realtime"
# anam_avatar_id    = "..."
# ssh_ingress_cidr  = "203.0.113.7/32"   # your IP, to SSH the LiveKit box
EOF
```

## 2. First apply (creates ECR + everything else)

```bash
terraform init
terraform apply
```

The Fargate service and the dispatch Lambda will be **unhealthy until their
images exist** — that's expected; we push them next, then redeploy. (If the
Lambda errors on first apply because its image isn't pushed yet, push the image
(step 3) and re-run `terraform apply`.)

## 3. Build & push the two images

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION=$(terraform output -raw region 2>/dev/null || echo us-east-1)
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

# from the avatar-agent/ root (one level up):
cd ..

# agent worker (the existing Dockerfile)
AGENT_REPO=$(cd deploy/terraform && terraform output -raw agent_ecr_repo)
docker build --platform linux/amd64 -t "$AGENT_REPO:latest" .
docker push "$AGENT_REPO:latest"

# dispatch Lambda
DISPATCH_REPO=$(cd deploy/terraform && terraform output -raw dispatch_ecr_repo)
docker build --platform linux/amd64 -f deploy/Dockerfile.lambda -t "$DISPATCH_REPO:latest" .
docker push "$DISPATCH_REPO:latest"

cd deploy/terraform
terraform apply   # picks up the Lambda image; (re)creates the function cleanly
```

## 4. Populate secrets in SSM

Terraform created the provider params with a `REPLACE_ME` placeholder. Set the
real values (they're `ignore_changes`d, so Terraform won't revert them):

```bash
P=sunstead-avatar   # = var.name_prefix
aws ssm put-parameter --overwrite --type SecureString --name "/$P/providers/recall_api_key"    --value "$RECALL_API_KEY"
aws ssm put-parameter --overwrite --type SecureString --name "/$P/providers/anam_api_key"      --value "$ANAM_API_KEY"
aws ssm put-parameter --overwrite --type SecureString --name "/$P/providers/anthropic_api_key" --value "$ANTHROPIC_API_KEY"
aws ssm put-parameter --overwrite --type SecureString --name "/$P/providers/deepgram_api_key"  --value "$DEEPGRAM_API_KEY"
aws ssm put-parameter --overwrite --type SecureString --name "/$P/providers/cartesia_api_key"  --value "$CARTESIA_API_KEY"
# openai_api_key only if PIPELINE_MODE=realtime; backend_token if your backend needs it
```

`terraform output ssm_secret_params` lists them all. The LiveKit key/secret are
**generated** by Terraform — no action needed.

## 5. DNS for LiveKit

```bash
terraform output livekit_eip
```

Create two **A records** pointing at that IP:

- `livekit.sunstead.example.com   → <eip>`
- `turn.sunstead.example.com      → <eip>`

Caddy on the box issues Let's Encrypt certs once DNS resolves (it retries
automatically). Verify: `curl -I https://<livekit_domain>` returns a LiveKit 200.

## 6. Upload the viewer

```bash
BUCKET=$(terraform output -raw viewer_bucket)
DIST=$(terraform output -raw viewer_distribution_id)
aws s3 cp ../../viewer/index.html "s3://$BUCKET/index.html" --content-type text/html
aws cloudfront create-invalidation --distribution-id "$DIST" --paths '/index.html'
```

## 7. Roll out the agent + go

```bash
aws ecs update-service \
  --cluster "$(terraform output -raw ecs_cluster)" \
  --service "$(terraform output -raw agent_service)" \
  --force-new-deployment

# send the avatar into a meeting:
curl -X POST "$(terraform output -raw dispatch_function_url)" \
  -H 'content-type: application/json' \
  -d '{"meeting_url":"https://meet.google.com/abc-defg-hij"}'
```

Wire `meet-joiner`'s `/api/join` route to that Function URL and the front-end
sends the bot in.

## Operating notes

- **Redeploy the worker** after pushing a new image: step 7's `update-service`
  (the service `ignore_changes` desired_count, so Terraform won't fight you).
- **Logs**: `aws logs tail /ecs/sunstead-avatar-agent --follow`. LiveKit logs:
  SSH the box, `docker compose -f /opt/livekit/docker-compose.yaml logs -f`.
- **Call artifacts** (transcript + tool timeline) write to the task's ephemeral
  `/tmp` today. To keep them, grant the `agent_task` role S3 access and point
  `ARTIFACT_DIR` at a mounted/uploaded location.
- **Cost**: the EC2 SFU + Fargate task run 24/7. Scale the SFU instance type for
  real concurrency; `agent_desired_count` for more simultaneous meetings.

## Hardening

- **Lock down dispatch.** The Function URL is `authorization_type = NONE`. Switch
  to `AWS_IAM` (and sign requests) or front it with API Gateway + an API key so
  only `meet-joiner` can call it.
- **TURN/TLS (443).** Omitted for a robust bootstrap. Add it if clients sit
  behind firewalls blocking UDP and non-443 TCP — LiveKit must read Caddy's cert
  files; see LiveKit's self-hosting docs.
- **Private subnets.** The worker runs in public subnets with a public IP for
  egress (no NAT, cheaper). For stricter egress control, add a NAT gateway and
  move the Fargate tasks to private subnets.
- **LiveKit HA.** This is single-node. Multi-node needs Redis and a load
  balancer in front of the SFU.
