# The LiveKit agent worker on ECS Fargate — always-on, horizontally scalable.
# It dials out to the self-hosted LiveKit (egress only, no inbound), so it runs
# in the public subnets with a public IP for egress and no load balancer.

resource "aws_ecs_cluster" "main" {
  name = "${var.name_prefix}-cluster"
}

resource "aws_cloudwatch_log_group" "agent" {
  name              = "/ecs/${var.name_prefix}-agent"
  retention_in_days = 14
}

# ── IAM: execution role (pull image, read SSM secrets, write logs) ──
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "agent_execution" {
  name               = "${var.name_prefix}-agent-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "agent_execution_managed" {
  role       = aws_iam_role.agent_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# All SSM SecureString params the task injects as secrets.
locals {
  agent_secret_arns = [
    aws_ssm_parameter.livekit_api_key.arn,
    aws_ssm_parameter.livekit_api_secret.arn,
    aws_ssm_parameter.provider_secret["anthropic_api_key"].arn,
    aws_ssm_parameter.provider_secret["deepgram_api_key"].arn,
    aws_ssm_parameter.provider_secret["cartesia_api_key"].arn,
    aws_ssm_parameter.provider_secret["openai_api_key"].arn,
    aws_ssm_parameter.provider_secret["anam_api_key"].arn,
    aws_ssm_parameter.provider_secret["backend_token"].arn,
  ]
}

data "aws_iam_policy_document" "agent_secrets" {
  statement {
    actions   = ["ssm:GetParameters"]
    resources = local.agent_secret_arns
  }
  # SecureString params are encrypted with the AWS-managed SSM key.
  statement {
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "agent_secrets" {
  name   = "${var.name_prefix}-agent-secrets"
  role   = aws_iam_role.agent_execution.id
  policy = data.aws_iam_policy_document.agent_secrets.json
}

# Task role (the app's own AWS identity). Minimal today — add S3 here if you
# later ship call artifacts off the ephemeral task filesystem.
resource "aws_iam_role" "agent_task" {
  name               = "${var.name_prefix}-agent-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# ── Security group: egress only ──
resource "aws_security_group" "agent" {
  name        = "${var.name_prefix}-agent"
  description = "Agent worker — egress only"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name_prefix}-agent" }
}

# ── Task definition ──
resource "aws_ecs_task_definition" "agent" {
  family                   = "${var.name_prefix}-agent"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.agent_cpu
  memory                   = var.agent_memory
  execution_role_arn       = aws_iam_role.agent_execution.arn
  task_role_arn            = aws_iam_role.agent_task.arn

  container_definitions = jsonencode([
    {
      name      = "agent"
      image     = "${aws_ecr_repository.agent.repository_url}:${var.agent_image_tag}"
      essential = true

      environment = [
        { name = "PIPELINE_MODE", value = var.pipeline_mode },
        { name = "LLM_MODEL", value = var.llm_model },
        { name = "STT_MODEL", value = var.stt_model },
        { name = "LIVEKIT_URL", value = local.livekit_ws_url },
        { name = "BOT_NAME", value = var.bot_name },
        { name = "ANAM_AVATAR_NAME", value = var.anam_avatar_name },
        { name = "ANAM_AVATAR_ID", value = var.anam_avatar_id },
        { name = "BACKEND_URL", value = var.backend_url },
        { name = "BACKEND_TIMEOUT", value = "0.3" },
        { name = "ARTIFACT_DIR", value = "/tmp/artifacts" },
        { name = "LOG_LEVEL", value = "INFO" },
      ]

      secrets = [
        { name = "LIVEKIT_API_KEY", valueFrom = aws_ssm_parameter.livekit_api_key.arn },
        { name = "LIVEKIT_API_SECRET", valueFrom = aws_ssm_parameter.livekit_api_secret.arn },
        { name = "ANTHROPIC_API_KEY", valueFrom = aws_ssm_parameter.provider_secret["anthropic_api_key"].arn },
        { name = "DEEPGRAM_API_KEY", valueFrom = aws_ssm_parameter.provider_secret["deepgram_api_key"].arn },
        { name = "CARTESIA_API_KEY", valueFrom = aws_ssm_parameter.provider_secret["cartesia_api_key"].arn },
        { name = "OPENAI_API_KEY", valueFrom = aws_ssm_parameter.provider_secret["openai_api_key"].arn },
        { name = "ANAM_API_KEY", valueFrom = aws_ssm_parameter.provider_secret["anam_api_key"].arn },
        { name = "BACKEND_TOKEN", valueFrom = aws_ssm_parameter.provider_secret["backend_token"].arn },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.agent.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "agent"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "agent" {
  name            = "${var.name_prefix}-agent"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.agent.arn
  desired_count   = var.agent_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.agent.id]
    assign_public_ip = true # needed for egress without a NAT gateway
  }

  # Avoid thrashing the service on every `terraform apply` after you push a new
  # image with the same tag; force redeploys with the AWS CLI (see README).
  lifecycle {
    ignore_changes = [desired_count]
  }
}
