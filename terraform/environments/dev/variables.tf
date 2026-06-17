variable "prefix" {
  description = "Prefix for naming resources"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
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
