variable "prefix" {
  description = "Prefix for naming resources"
  type        = string
}

variable "lambda_function_arn" {
  description = "ARN of the Lambda function to send events to"
  type        = string
}