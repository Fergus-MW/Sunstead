# Self-hosted LiveKit SFU on a single EC2 instance with an Elastic IP.
# Not Fargate: WebRTC needs a wide UDP port range + a stable public IP, which
# fits a VM with host networking, not a Fargate task.

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

resource "aws_security_group" "livekit" {
  name        = "${var.name_prefix}-livekit"
  description = "LiveKit SFU: signaling, media, TURN"
  vpc_id      = aws_vpc.main.id

  # HTTP (Caddy ACME challenge + redirect)
  ingress {
    description = "http (ACME)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # HTTPS / WSS signaling
  ingress {
    description = "https/wss signaling"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # ICE/TCP fallback
  ingress {
    description = "rtc tcp"
    from_port   = 7881
    to_port     = 7881
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # WebRTC media (UDP range)
  ingress {
    description = "rtc udp media"
    from_port   = 50000
    to_port     = 60000
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # TURN/UDP
  ingress {
    description = "turn udp"
    from_port   = 3478
    to_port     = 3478
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name_prefix}-livekit" }
}

# Optional SSH ingress, only when a CIDR is supplied.
resource "aws_security_group_rule" "livekit_ssh" {
  count             = var.ssh_ingress_cidr == "" ? 0 : 1
  type              = "ingress"
  description       = "ssh"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = [var.ssh_ingress_cidr]
  security_group_id = aws_security_group.livekit.id
}

locals {
  livekit_yaml = templatefile("${path.module}/templates/livekit.yaml.tftpl", {
    api_key     = aws_ssm_parameter.livekit_api_key.value
    api_secret  = aws_ssm_parameter.livekit_api_secret.value
    turn_domain = var.livekit_turn_domain
  })

  caddyfile = templatefile("${path.module}/templates/Caddyfile.tftpl", {
    livekit_domain = var.livekit_domain
    acme_email     = var.acme_email
  })

  livekit_user_data = templatefile("${path.module}/templates/livekit_user_data.sh.tftpl", {
    livekit_yaml = local.livekit_yaml
    caddyfile    = local.caddyfile
  })
}

resource "aws_instance" "livekit" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.livekit_instance_type
  subnet_id              = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.livekit.id]
  key_name               = var.ssh_key_name == "" ? null : var.ssh_key_name
  user_data              = local.livekit_user_data

  # Re-run user_data if the rendered config changes (new keys / domains).
  user_data_replace_on_change = true

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  tags = { Name = "${var.name_prefix}-livekit" }
}

resource "aws_eip" "livekit" {
  instance = aws_instance.livekit.id
  domain   = "vpc"
  tags     = { Name = "${var.name_prefix}-livekit" }
}

locals {
  # The agent worker, viewer, and dispatch Lambda all use this URL.
  livekit_ws_url = "wss://${var.livekit_domain}"
}
