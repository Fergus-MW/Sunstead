# Build → push → roll out the container images as part of `terraform apply`.
#
# The image tag is a short hash of the source tree, so the tag changes only when
# the code does. A changed tag re-registers the ECS task definition (which the
# service rolls out automatically) and updates the Lambda's image_uri; an
# unchanged tree is a no-op — the build provisioner doesn't re-run. This replaces
# the old manual "docker build/push + aws ecs update-service --force-new-deployment"
# dance. Set auto_build=false to keep that manual flow and pin tags via the
# *_image_tag variables instead.

locals {
  repo_root = "${path.module}/../.."

  # Files whose contents define each image. Hashing them makes the tag track the
  # code: the agent image is built from src/ + Dockerfile; the dispatch (Lambda)
  # image from src/ + deploy/Dockerfile.lambda. Both share the Python deps.
  agent_src_hash = substr(sha1(join("", concat(
    [for f in fileset(local.repo_root, "src/**") : filesha1("${local.repo_root}/${f}") if !strcontains(f, "__pycache__")],
    [
      filesha1("${local.repo_root}/Dockerfile"),
      filesha1("${local.repo_root}/pyproject.toml"),
      filesha1("${local.repo_root}/uv.lock"),
    ],
  ))), 0, 12)

  dispatch_src_hash = substr(sha1(join("", concat(
    [for f in fileset(local.repo_root, "src/**") : filesha1("${local.repo_root}/${f}") if !strcontains(f, "__pycache__")],
    [
      filesha1("${local.repo_root}/deploy/Dockerfile.lambda"),
      filesha1("${local.repo_root}/pyproject.toml"),
      filesha1("${local.repo_root}/uv.lock"),
    ],
  ))), 0, 12)

  # auto_build → deploy the hash tag terraform just built; otherwise honor the
  # manually-pinned *_image_tag (the pre-existing out-of-band push workflow).
  agent_image_tag_effective    = var.auto_build ? local.agent_src_hash : var.agent_image_tag
  dispatch_image_tag_effective = var.auto_build ? local.dispatch_src_hash : var.dispatch_image_tag

  # "<account>.dkr.ecr.<region>.amazonaws.com" — the host to `docker login` to.
  ecr_registry = split("/", aws_ecr_repository.agent.repository_url)[0]
}

# Build recipe version — bump to force a rebuild when the *command* changes (not
# the source). Lambda rejects buildx's default OCI manifest, so both images are
# built with `--provenance=false` and pushed as a Docker v2 manifest (ECS accepts
# either; Lambda requires this). Included in triggers_replace below.
locals {
  build_recipe = "v2-docker-manifest"
}

# Agent worker image (ECS Fargate). Rebuilt+pushed only when the source or the
# build recipe changes. buildx `--push` emits a single-platform Docker manifest.
resource "terraform_data" "agent_build" {
  count            = var.auto_build ? 1 : 0
  triggers_replace = "${local.agent_src_hash}-${local.build_recipe}"

  provisioner "local-exec" {
    working_dir = local.repo_root
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      aws ecr get-login-password --region ${var.region} \
        | docker login --username AWS --password-stdin ${local.ecr_registry}
      docker buildx build --platform linux/amd64 --provenance=false --push \
        -t ${aws_ecr_repository.agent.repository_url}:${local.agent_src_hash} \
        -t ${aws_ecr_repository.agent.repository_url}:latest .
    EOT
  }
}

# Dispatch Lambda image. Lambda is strict about the manifest media type, so the
# --provenance=false Docker manifest is mandatory here (not just nice-to-have).
resource "terraform_data" "dispatch_build" {
  count            = var.auto_build ? 1 : 0
  triggers_replace = "${local.dispatch_src_hash}-${local.build_recipe}"

  # Serialize after the agent build: both run `docker login` to the same registry,
  # and concurrent logins race on the macOS keychain credential store
  # ("item already exists in the keychain (-25299)"). Sequential logins are safe.
  depends_on = [terraform_data.agent_build]

  provisioner "local-exec" {
    working_dir = local.repo_root
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      aws ecr get-login-password --region ${var.region} \
        | docker login --username AWS --password-stdin ${local.ecr_registry}
      docker buildx build --platform linux/amd64 --provenance=false --push \
        -f deploy/Dockerfile.lambda \
        -t ${aws_ecr_repository.dispatch.repository_url}:${local.dispatch_src_hash} \
        -t ${aws_ecr_repository.dispatch.repository_url}:latest .
    EOT
  }
}
