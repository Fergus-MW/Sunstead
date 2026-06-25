# Dispatch Lambda (container image) behind a Function URL. The meet-joiner
# front-end POSTs { "meeting_url": "..." } here to send the avatar into a call,
# without ever holding Recall/LiveKit credentials in the browser.

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dispatch" {
  name               = "${var.name_prefix}-dispatch"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "dispatch_logs" {
  role       = aws_iam_role.dispatch.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# The handler reads LiveKit creds + Recall key from SSM at runtime.
locals {
  dispatch_secret_arns = [
    aws_ssm_parameter.livekit_api_key.arn,
    aws_ssm_parameter.livekit_api_secret.arn,
    aws_ssm_parameter.provider_secret["recall_api_key"].arn,
  ]
}

data "aws_iam_policy_document" "dispatch_secrets" {
  statement {
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = local.dispatch_secret_arns
  }
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

resource "aws_iam_role_policy" "dispatch_secrets" {
  name   = "${var.name_prefix}-dispatch-secrets"
  role   = aws_iam_role.dispatch.id
  policy = data.aws_iam_policy_document.dispatch_secrets.json
}

resource "aws_lambda_function" "dispatch" {
  function_name = "${var.name_prefix}-dispatch"
  role          = aws_iam_role.dispatch.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.dispatch.repository_url}:${local.dispatch_image_tag_effective}"
  timeout       = 30
  memory_size   = 256

  environment {
    variables = {
      LIVEKIT_URL   = local.livekit_ws_url
      VIEWER_URL    = local.viewer_url
      BOT_NAME      = var.bot_name
      RECALL_REGION = var.recall_region
      # Resolve SSM SecureStrings at cold start via the pydantic-settings env.
      # (Lambda can't natively inject SSM like ECS, so the handler reads them.)
      LIVEKIT_API_KEY_SSM    = aws_ssm_parameter.livekit_api_key.name
      LIVEKIT_API_SECRET_SSM = aws_ssm_parameter.livekit_api_secret.name
      RECALL_API_KEY_SSM     = aws_ssm_parameter.provider_secret["recall_api_key"].name
    }
  }

  # The image must exist in ECR before this applies cleanly — see README order.
  depends_on = [aws_ecr_repository.dispatch, terraform_data.dispatch_build]
}

resource "aws_lambda_function_url" "dispatch" {
  function_name      = aws_lambda_function.dispatch.function_name
  authorization_type = "NONE" # public; front-end calls it directly. See README to lock down.
}

# A Function URL with auth NONE still needs an explicit public-invoke permission.
resource "aws_lambda_permission" "dispatch_url" {
  statement_id           = "AllowPublicFunctionUrlInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.dispatch.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}
