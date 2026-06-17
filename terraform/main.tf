terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
      configuration_aliases = [
        aws.us_east_1
      ]
    }
  }
}

variable "prefix" {
  description = "Prefix for naming resources"
  type        = string

  validation {
    condition     = length(trimspace(var.prefix)) > 0
    error_message = "The prefix must not be empty or contain only whitespace."
  }
}

variable "environment" {
  description = "Deployment environment"
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "The environment must be dev, staging, or prod."
  }
}

variable "lambda_zip_path" {
  description = "Path to the Lambda deployment package"
  type        = string
}

variable "slack_webhook_ssm_param" {
  description = "SSM Parameter Store path containing the Slack webhook URL"
  type        = string
}

variable "dry_run_mode" {
  description = "turn on or off dry-run mode"
  type        = bool
  default     = true
}

variable "reserved_concurrency" {
  type    = number
  default = null
}

module "cloudwatch" {
  source = "./modules/cloudwatch"

  function_name = "${var.prefix}-event-processor"
  environment   = var.environment
  kms_key_arn   = aws_kms_key.app.arn
}

module "lambda" {
  source = "./modules/lambda"

  prefix                   = var.prefix
  lambda_zip_path          = var.lambda_zip_path
  dry_run_mode             = var.dry_run_mode
  reserved_concurrency     = var.reserved_concurrency
  slack_webhook_ssm_param  = var.slack_webhook_ssm_param
  cloudwatch_log_group_arn = module.cloudwatch.log_group_arn
  kms_key_arn              = aws_kms_key.app.arn
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  app_region_default_event_bus_arn = "arn:aws:events:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:event-bus/default"
}

resource "aws_kms_key" "app" {
  description             = "App KMS key for CloudWatch logs, Lambda env, and SQS DLQ"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowAccountAdmin"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowCloudWatchLogs"
        Effect = "Allow"
        Principal = {
          Service = "logs.${data.aws_region.current.region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*"
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:*"
          }
        }
      }
      ,
      {
        Sid    = "AllowCloudTrailKMS"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey*",
          "kms:DescribeKey"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:SourceArn" = "arn:aws:cloudtrail:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:trail/${var.prefix}-management-events"
          }
          StringLike = {
            "kms:EncryptionContext:aws:cloudtrail:arn" = "arn:aws:cloudtrail:*:${data.aws_caller_identity.current.account_id}:trail/*"
          }
        }
      }
    ]
  })
}

resource "aws_s3_bucket" "cloudtrail_logs" {
  # checkov:skip=CKV_AWS_18: Access logging for this CloudTrail log bucket is deferred to avoid a second logging bucket in the dev footprint.
  # checkov:skip=CKV2_AWS_62: Event notifications are not needed for CloudTrail delivery; EventBridge consumes management events from CloudTrail directly.
  # checkov:skip=CKV_AWS_144: Cross-region replication is intentionally out of scope for the dev learning environment.
  # checkov:skip=CKV_AWS_145: The bucket uses SSE-S3; the CloudTrail trail itself is encrypted with the application KMS key.
  bucket = "${var.prefix}-cloudtrail-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "cloudtrail_logs" {
  bucket = aws_s3_bucket.cloudtrail_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "cloudtrail_logs" {
  bucket = aws_s3_bucket.cloudtrail_logs.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "cloudtrail_logs" {
  bucket = aws_s3_bucket.cloudtrail_logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail_logs" {
  bucket = aws_s3_bucket.cloudtrail_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "cloudtrail_logs" {
  bucket = aws_s3_bucket.cloudtrail_logs.id

  rule {
    id     = "expire-cloudtrail-logs"
    status = "Enabled"

    filter {
      prefix = ""
    }

    expiration {
      days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_bucket_policy" "cloudtrail_logs" {
  bucket = aws_s3_bucket.cloudtrail_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AWSCloudTrailAclCheck"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.cloudtrail_logs.arn
      },
      {
        Sid    = "AWSCloudTrailWrite"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.cloudtrail_logs.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      }
    ]
  })
}

resource "aws_cloudtrail" "management_events" {
  # checkov:skip=CKV_AWS_252: SNS delivery notifications are not required; EventBridge and Lambda logs are the operational paths for this project.
  # checkov:skip=CKV2_AWS_10: Direct CloudTrail-to-CloudWatch integration is deferred; CloudWatch audit logs are produced by the processing Lambdas.
  name                          = "${var.prefix}-management-events"
  s3_bucket_name                = aws_s3_bucket.cloudtrail_logs.id
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_logging                = true
  enable_log_file_validation    = true
  kms_key_id                    = aws_kms_key.app.arn

  event_selector {
    read_write_type           = "WriteOnly"
    include_management_events = true
  }

  depends_on = [aws_s3_bucket_policy.cloudtrail_logs]
}

module "eventbridge" {
  source              = "./modules/eventbridge"
  prefix              = var.prefix
  lambda_function_arn = module.lambda.function_arn
}

resource "aws_iam_role" "iam_global_event_forwarder" {
  provider = aws.us_east_1

  name = "${var.prefix}-iam-global-event-forwarder"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "iam_global_event_forwarder" {
  provider = aws.us_east_1

  name = "${var.prefix}-iam-global-event-forwarder"
  role = aws_iam_role.iam_global_event_forwarder.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "events:PutEvents"
        Resource = local.app_region_default_event_bus_arn
      }
    ]
  })
}

resource "aws_cloudwatch_event_rule" "iam_create_access_key_global" {
  provider = aws.us_east_1

  name        = "${var.prefix}-iam-create-access-key-global"
  description = "Forward IAM CreateAccessKey global service events to the application region"

  event_pattern = jsonencode({
    source      = ["aws.iam"],
    detail-type = ["AWS API Call via CloudTrail"],
    detail = {
      eventName = ["CreateAccessKey"]
    }
  })
}

resource "aws_cloudwatch_event_target" "iam_create_access_key_global_to_app_region" {
  provider = aws.us_east_1

  rule      = aws_cloudwatch_event_rule.iam_create_access_key_global.name
  target_id = "ForwardToAppRegionDefaultBus"
  arn       = local.app_region_default_event_bus_arn
  role_arn  = aws_iam_role.iam_global_event_forwarder.arn
}

output "approval_callback_url" {
  description = "Slack Interactivity callback URL for approval actions"
  value       = module.lambda.approval_callback_url
}

output "approval_table_name" {
  description = "DynamoDB table storing approval requests"
  value       = module.lambda.approval_table_name
}

output "cloudtrail_name" {
  description = "CloudTrail trail used to deliver management API events to EventBridge"
  value       = aws_cloudtrail.management_events.name
}
