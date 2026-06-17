variable "function_name" {
  description = "Name of the Lambda function"
  type        = string

  validation {
    condition     = length(trimspace(var.function_name)) > 0
    error_message = "The function_name must not be empty or contain only whitespace."
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

variable "kms_key_arn" {
  description = "ARN of the KMS key for encrypting Lambda environment variables and CloudWatch logs"
  type        = string
}
