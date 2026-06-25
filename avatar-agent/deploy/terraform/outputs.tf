output "livekit_eip" {
  description = "LiveKit server IP. With a custom domain, point its A record(s) here."
  value       = aws_eip.livekit.public_ip
}

output "livekit_hostname" {
  description = "Effective LiveKit hostname (auto sslip.io unless you set livekit_domain)."
  value       = local.livekit_domain_effective
}

output "livekit_ws_url" {
  description = "LiveKit signaling URL the agent/viewer/dispatch use."
  value       = local.livekit_ws_url
}

output "viewer_url" {
  description = "Public HTTPS URL of the avatar viewer page (Recall camera source)."
  value       = local.viewer_url
}

output "viewer_bucket" {
  description = "S3 bucket to upload viewer/index.html into."
  value       = aws_s3_bucket.viewer.bucket
}

output "viewer_distribution_id" {
  description = "CloudFront distribution id (for cache invalidation after re-upload)."
  value       = aws_cloudfront_distribution.viewer.id
}

output "agent_ecr_repo" {
  description = "Push the agent worker image here."
  value       = aws_ecr_repository.agent.repository_url
}

output "dispatch_ecr_repo" {
  description = "Push the dispatch Lambda image here."
  value       = aws_ecr_repository.dispatch.repository_url
}

output "agent_image" {
  description = "Full agent image (repo:tag) the Fargate service is running."
  value       = "${aws_ecr_repository.agent.repository_url}:${local.agent_image_tag_effective}"
}

output "dispatch_image" {
  description = "Full dispatch image (repo:tag) the Lambda is running."
  value       = "${aws_ecr_repository.dispatch.repository_url}:${local.dispatch_image_tag_effective}"
}

output "ecs_cluster" {
  description = "ECS cluster name (for force-new-deployment)."
  value       = aws_ecs_cluster.main.name
}

output "agent_service" {
  description = "ECS service name (for force-new-deployment)."
  value       = aws_ecs_service.agent.name
}

output "dispatch_function_url" {
  description = "POST { meeting_url } here to send the avatar into a Meet."
  value       = aws_lambda_function_url.dispatch.function_url
}

output "ssm_secret_params" {
  description = "SSM parameters to populate with real values before first run."
  value       = [for p in aws_ssm_parameter.provider_secret : p.name]
}
