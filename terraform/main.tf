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
  slack_webhook_ssm_param  = var.slack_webhook_ssm_param
  cloudwatch_log_group_arn = module.cloudwatch.log_group_arn
  kms_key_arn              = aws_kms_key.app.arn
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

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
    ]
  })
}
