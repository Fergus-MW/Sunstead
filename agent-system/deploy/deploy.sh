#!/usr/bin/env bash
# One-command bring-up for the agent suite on the EC2 host (run from anywhere):
#   bash deploy/deploy.sh
# Preflight-checks the secrets, builds + starts the Aiven+Cloudflare stack, waits for health.
set -euo pipefail

cd "$(dirname "$0")/.."   # -> agent-system/
COMPOSE="docker compose -f docker-compose.aiven.yml -f docker-compose.cloudflared.yml"

echo "== preflight =="
missing=0
for f in .env ca.pem service.cert service.key; do
  if [ ! -f "$f" ]; then echo "  MISSING: agent-system/$f"; missing=1; else echo "  ok: $f"; fi
done
for key in ANTHROPIC_API_KEY AIVEN_TOKEN KAFKA_BOOTSTRAP CLOUDFLARE_TUNNEL_TOKEN; do
  if ! grep -qE "^${key}=.+" .env; then echo "  MISSING in .env: ${key}"; missing=1; fi
done
if grep -qE '^KAFKA_SECURITY=SASL_SSL' .env; then
  echo "  WARN: KAFKA_SECURITY=SASL_SSL — kafka-254bd14f uses certificate auth; set KAFKA_SECURITY=SSL"
fi
[ "$missing" -eq 0 ] || { echo "preflight FAILED — fix the above, then rerun."; exit 1; }

echo "== build + up (first build pulls deps + mcp-aiven, a few minutes) =="
$COMPOSE up --build -d

echo "== waiting for the gateway to answer on :8800 =="
for i in $(seq 1 40); do
  if curl -sf http://localhost:8800/health >/dev/null 2>&1; then echo "  gateway healthy"; break; fi
  [ "$i" -eq 40 ] && { echo "  gateway not healthy yet — check: $COMPOSE logs gateway"; }
  sleep 3
done

echo "== status =="
$COMPOSE ps
cat <<EOF

Done. Useful:
  logs:    $COMPOSE logs -f planner runner
  update:  git pull && $COMPOSE up --build -d
  stop:    $COMPOSE down
Verify publicly once the tunnel hostname is mapped:
  curl https://gateway.<your-domain>/health
EOF
