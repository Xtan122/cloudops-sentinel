variable "state_bucket_name" {
  description = "Globally unique S3 bucket name for Terraform remote state"
  type        = string

  validation {
    condition     = length(trimspace(var.state_bucket_name)) > 0
    error_message = "The state_bucket_name variable must not be empty."
  }
}

variable "aws_region" {
  description = "AWS region for state backend"
  type        = string
  default     = "ap-southeast-1"
}
