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

# ── DNS (self-hosted LiveKit needs TLS, which needs a hostname) ──
# Leave both blank to auto-derive a hostname from the Elastic IP via sslip.io
# (e.g. 1-2-3-4.sslip.io) — Caddy still gets a real Let's Encrypt cert, with zero
# DNS setup. Set them to use your own domain instead (more robust; no shared-
# domain cert rate limits) and point A records at the livekit_eip output.
variable "livekit_domain" {
  description = "LiveKit signaling FQDN, e.g. livekit.example.com. Blank = auto sslip.io from the EIP."
  type        = string
  default     = ""
}

variable "livekit_turn_domain" {
  description = "TURN FQDN, e.g. turn.example.com. Blank = reuse the signaling hostname."
  type        = string
  default     = ""
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

# ── Fargate agent worker ──
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

variable "agent_image_tag" {
  description = "Image tag the Fargate service runs when auto_build=false (push it yourself)."
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
  description = "Cognition pipeline: \"cascade\" (Deepgram+Anthropic+Cartesia) or \"realtime\" (OpenAI)."
  type        = string
  default     = "cascade"
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

variable "recall_region" {
  description = "Recall.ai region your API key belongs to (must match the key, e.g. eu-central-1)."
  type        = string
  default     = "us-east-1"
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

# ── Dispatch Lambda ──
variable "dispatch_image_tag" {
  description = "Image tag for the dispatch Lambda (push to its ECR repo)."
  type        = string
  default     = "latest"
}
