resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = 90

  tags = {
    Project     = "CloudOps Sentinel"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}
