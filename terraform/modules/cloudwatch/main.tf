resource "aws_cloudwatch_log_group" "lambda_logs" {
  # checkov:skip=CKV_AWS_338: REQ-12.6 requires 90-day retention; 1-year retention is intentionally deferred for cost control per REQ-18.3.

  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = 90
  kms_key_id        = var.kms_key_arn

  tags = {
    Project     = "CloudOps Sentinel"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}
