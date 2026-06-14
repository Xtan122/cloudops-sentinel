variable "prefix" {
  description = "Prefix for naming resources"
  type        = string
}

variable "lambda_zip_path" {
  description = "Path to the Lambda deployment package"
  type        = string
}

variable "dry_run_mode" {
  description = "turn on or off dry-run mode"
  type        = bool
  default     = true
}

variable "slack_webhook_ssm_param" {
  description = "SSM Parameter Store path containing the Slack webhook URL"
  type        = string
}

variable "memory_size" {
  description = "Lambda memory size"
  type        = number
  default     = 256

  validation {
    condition     = var.memory_size >= 128 && var.memory_size <= 512
    error_message = "memory_size must be between 128 and 512 MB."
  }
}

variable "timeout_seconds" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 25

  validation {
    condition     = var.timeout_seconds > 0 && var.timeout_seconds < 30
    error_message = "timeout_seconds must be greater than 0 and less than 30."
  }
}

variable "reserved_concurrency" {
  description = "Reserved concurrency for the Lambda function"
  type        = number
  default     = 10

  validation {
    condition = (
      var.reserved_concurrency > 0
      && var.reserved_concurrency == floor(var.reserved_concurrency)
    )
    error_message = "reserved_concurrency must be a positive integer."
  }
}

variable "cloudwatch_log_group_arn" {
  description = "ARN of the CloudWatch log group for Lambda"
  type        = string

  validation {
    condition     = length(trimspace(var.cloudwatch_log_group_arn)) > 0
    error_message = "The cloudwatch_log_group_arn must not be empty or contain only whitespace."
  }
}