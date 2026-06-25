#!/usr/bin/env bash
# Idempotent provisioning for the Sunstead Aiven services.
#
# What this creates (in Aiven project AIVEN_PROJECT):
#   - Postgres 17 (central-kg-pg, free-1-1gb, do-lon)
#   - OpenSearch 3.x (central-kg-os, free-4-20, do-lon)
#   - Kafka            (central-kg-kafka, ...)   ← TODO: turn on when needed
#
# It only *creates* services that don't exist; it does not destroy anything.
# Connection strings are NOT written to the repo — fetch them yourself via
# `avn service get` once the services are running.
#
# Prereqs:
#   brew install aiven-client
#   avn user login <you@example.com> --token
#
# Override the defaults with env vars before running, e.g.:
#   AIVEN_PROJECT=jq01 AIVEN_CLOUD=do-lon ./infra/provision.sh
#
# To attack the Aiven MCP challenge "in commits / on camera", run the same
# operations through the `aiven-mcp` server in Claude Code:
#   aiven_service_create(project="jq01", service_type="pg", ...)

set -euo pipefail

AIVEN_PROJECT="${AIVEN_PROJECT:-jq01}"
AIVEN_CLOUD="${AIVEN_CLOUD:-do-lon}"   # DigitalOcean London — free tier eligible
PG_NAME="${PG_NAME:-central-kg-pg}"
PG_PLAN="${PG_PLAN:-free-1-1gb}"
OS_NAME="${OS_NAME:-central-kg-os}"
OS_PLAN="${OS_PLAN:-free-4-20}"
KAFKA_NAME="${KAFKA_NAME:-central-kg-kafka}"
KAFKA_PLAN="${KAFKA_PLAN:-business-4}"  # no free Kafka — flip to a paid plan when you're ready

create_if_missing () {
  local name="$1" type="$2" plan="$3"
  if avn service get "$name" --project "$AIVEN_PROJECT" >/dev/null 2>&1; then
    echo "✓ $type service '$name' already exists — leaving it alone"
  else
    echo "→ creating $type service '$name' ($plan, $AIVEN_CLOUD)..."
    avn service create "$name" \
      --project "$AIVEN_PROJECT" \
      --service-type "$type" \
      --cloud "$AIVEN_CLOUD" \
      --plan "$plan"
  fi
}

create_if_missing "$PG_NAME"    pg         "$PG_PLAN"
create_if_missing "$OS_NAME"    opensearch "$OS_PLAN"
# create_if_missing "$KAFKA_NAME" kafka      "$KAFKA_PLAN"

echo
echo "Waiting for services to reach RUNNING..."
avn service wait "$PG_NAME" --project "$AIVEN_PROJECT" --timeout 600
avn service wait "$OS_NAME" --project "$AIVEN_PROJECT" --timeout 600

echo
echo "All services up. Next steps:"
echo "  1. avn service user-password-reset --project $AIVEN_PROJECT \\"
echo "         --username avnadmin --new-password '<choose>' $PG_NAME"
echo "  2. avn service user-password-reset --project $AIVEN_PROJECT \\"
echo "         --username avnadmin --new-password '<choose>' $OS_NAME"
echo "  3. psql \"\$PG_URI\" -f central-kg-api/schema.sql"
echo "  4. write the passwords into central-kg-api/.env (gitignored)"
