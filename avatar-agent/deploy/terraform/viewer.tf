# The viewer page (viewer/index.html) served over HTTPS via S3 + CloudFront.
# HTTPS is mandatory: Recall's browser needs a secure context for getUserMedia
# and to open a wss:// connection to LiveKit. The bucket is private; CloudFront
# reaches it through an Origin Access Control.

resource "aws_s3_bucket" "viewer" {
  bucket_prefix = "${var.name_prefix}-viewer-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "viewer" {
  bucket                  = aws_s3_bucket.viewer.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "viewer" {
  name                              = "${var.name_prefix}-viewer-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "viewer" {
  enabled             = true
  default_root_object = "index.html"
  comment             = "${var.name_prefix} avatar viewer"

  origin {
    domain_name              = aws_s3_bucket.viewer.bucket_regional_domain_name
    origin_id                = "viewer-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.viewer.id
  }

  default_cache_behavior {
    target_origin_id       = "viewer-s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]

    # AWS managed "CachingOptimized" policy.
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  price_class = "PriceClass_100"
}

# Let only this distribution read the bucket.
data "aws_iam_policy_document" "viewer_bucket" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.viewer.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.viewer.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "viewer" {
  bucket = aws_s3_bucket.viewer.id
  policy = data.aws_iam_policy_document.viewer_bucket.json
}

locals {
  # dispatch.py appends ?lk=…&token=… to this when creating the Recall bot.
  viewer_url = "https://${aws_cloudfront_distribution.viewer.domain_name}/index.html"
}
