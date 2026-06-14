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
}

module "lambda" {
  source = "./modules/lambda"

  prefix                   = var.prefix
  lambda_zip_path          = var.lambda_zip_path
  slack_webhook_ssm_param  = var.slack_webhook_ssm_param
  cloudwatch_log_group_arn = module.cloudwatch.log_group_arn
}
