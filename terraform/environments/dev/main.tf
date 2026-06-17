provider "aws" {
  region = "ap-southeast-1"
}

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

module "root" {
  source = "../.."

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  prefix                  = var.prefix
  environment             = var.environment
  lambda_zip_path         = var.lambda_zip_path
  dry_run_mode            = var.dry_run_mode
  reserved_concurrency    = var.reserved_concurrency
  slack_webhook_ssm_param = var.slack_webhook_ssm_param
}

output "approval_callback_url" {
  description = "Slack Interactivity callback URL for approval actions"
  value       = module.root.approval_callback_url
}

output "approval_table_name" {
  description = "DynamoDB table storing approval requests"
  value       = module.root.approval_table_name
}
