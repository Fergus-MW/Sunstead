# Secrets live in SSM Parameter Store (SecureString) — cheaper than Secrets
# Manager and natively injectable into ECS tasks and Lambda.
#
# Two kinds:
#  * LiveKit API key/secret — GENERATED here so the EC2 server, the agent, and
#    the dispatch Lambda all share the same credential.
#  * Third-party provider keys — created with a placeholder value; you set the
#    real value out-of-band (console / `aws ssm put-parameter --overwrite`).
#    `ignore_changes = [value]` stops Terraform from reverting your value.

# ── Generated LiveKit credential ──
resource "random_id" "livekit_api_key" {
  byte_length = 6
}

resource "random_password" "livekit_api_secret" {
  length  = 43
  special = false
}

resource "aws_ssm_parameter" "livekit_api_key" {
  name  = "/${var.name_prefix}/livekit/api_key"
  type  = "SecureString"
  value = "API${random_id.livekit_api_key.hex}"
}

resource "aws_ssm_parameter" "livekit_api_secret" {
  name  = "/${var.name_prefix}/livekit/api_secret"
  type  = "SecureString"
  value = random_password.livekit_api_secret.result
}

# ── Provider keys you must populate after apply ──
locals {
  provider_secret_names = [
    "anthropic_api_key",
    "deepgram_api_key",
    "cartesia_api_key",
    "openai_api_key",
    "anam_api_key",
    "recall_api_key",
    "backend_token",
  ]
}

resource "aws_ssm_parameter" "provider_secret" {
  for_each = toset(local.provider_secret_names)

  name  = "/${var.name_prefix}/providers/${each.key}"
  type  = "SecureString"
  value = "REPLACE_ME"

  lifecycle {
    ignore_changes = [value] # set the real value out-of-band; don't let TF revert it
  }
}
