variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for all resource names."
  type        = string
  default     = "sunstead-avatar"
}

# ── DNS (self-hosted LiveKit needs TLS, which needs a real domain) ──
variable "livekit_domain" {
  description = <<-EOT
    Fully-qualified domain for the LiveKit signaling endpoint, e.g.
    "livekit.sunstead.example.com". You must point an A record at the EIP this
    stack outputs (livekit_eip). Caddy on the instance auto-issues a Let's
    Encrypt cert for it, giving wss://<domain>.
  EOT
  type        = string
}

variable "livekit_turn_domain" {
  description = <<-EOT
    Domain for the embedded TURN/TLS server, e.g. "turn.sunstead.example.com".
    Point an A record at the same EIP. Used by clients on restrictive networks.
  EOT
  type        = string
}

variable "acme_email" {
  description = "Email Caddy registers with Let's Encrypt for cert notices."
  type        = string
}

# ── LiveKit EC2 ──
variable "livekit_instance_type" {
  description = "EC2 instance type for the LiveKit SFU. Media-heavy; size up for real load."
  type        = string
  default     = "c6i.large"
}

variable "ssh_ingress_cidr" {
  description = "CIDR allowed to SSH to the LiveKit box (set to your IP, or \"\" to disable SSH)."
  type        = string
  default     = ""
}

variable "ssh_key_name" {
  description = "Existing EC2 key pair name for SSH access (optional)."
  type        = string
  default     = ""
}

# ── Image build / rollout (build.tf) ──
variable "auto_build" {
  description = <<-EOT
    When true (default), `terraform apply` builds the agent + dispatch images
    from local source, pushes them to ECR tagged with a content hash, and rolls
    them out — one command deploys the latest code. Needs docker + aws CLI on
    PATH. Set false to keep the manual build/push flow and pin the *_image_tag
    variables yourself.
  EOT
  type        = bool
  default     = true
}

# ── Fargate agent worker ──
variable "agent_image_tag" {
  description = "Image tag the Fargate service runs (push to the ECR repo this stack creates)."
  type        = string
  default     = "latest"
}

variable "agent_cpu" {
  description = "Fargate task CPU units (1024 = 1 vCPU)."
  type        = number
  default     = 1024
}

variable "agent_memory" {
  description = "Fargate task memory (MiB)."
  type        = number
  default     = 2048
}

variable "agent_desired_count" {
  description = "Number of agent worker tasks. Each hosts one or a few concurrent meetings."
  type        = number
  default     = 1
}

variable "pipeline_mode" {
  description = "Cognition pipeline: \"realtime\" (OpenAI, needs only OPENAI_API_KEY) or \"cascade\" (Deepgram+Anthropic+Cartesia)."
  type        = string
  default     = "realtime"
}

variable "llm_model" {
  description = "Anthropic model for the cascade pipeline."
  type        = string
  default     = "claude-sonnet-4-6"
}

variable "bot_name" {
  description = "Display name the Recall bot uses in the meeting."
  type        = string
  default     = "Sunstead Avatar"
}

variable "anam_avatar_name" {
  description = "Anam persona display name."
  type        = string
  default     = "Sunstead"
}

variable "anam_avatar_id" {
  description = "Anam avatar id to render (not secret; from the Anam dashboard)."
  type        = string
  default     = ""
}

variable "stt_provider" {
  description = <<-EOT
    Cascade STT engine: "deepgram" (what this stack provisions a key for) or
    "soniox". If you set "soniox", also add soniox_api_key to the SSM provider
    secrets and inject SONIOX_API_KEY into the task — otherwise the worker raises
    on a missing key at session start.
  EOT
  type        = string
  default     = "deepgram"
}

variable "stt_model" {
  description = "Deepgram STT model for the cascade pipeline."
  type        = string
  default     = "nova-3"
}

variable "backend_url" {
  description = "Base URL of the backend API the custom tools call (e.g. central-kg-api)."
  type        = string
  default     = ""
}

variable "gateway_url" {
  description = <<-EOT
    Base URL of the agent-system gateway (the delegation edge). The avatar POSTs
    /tasks here for `delegate` and /transcript for each final utterance. Blank =
    delegation/transcript-feed off (degrades gracefully). If the gateway requires
    auth, also provision a gateway_token secret and inject GATEWAY_TOKEN.
  EOT
  type        = string
  default     = ""
}

# ── Dispatch Lambda ──
variable "dispatch_image_tag" {
  description = "Image tag for the dispatch Lambda (push to its ECR repo)."
  type        = string
  default     = "latest"
}
