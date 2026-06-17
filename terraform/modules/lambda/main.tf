data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_sqs_queue" "lambda_dlq" {
  name = "${var.prefix}-lambda-dlq"

  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300

  message_retention_seconds = 1209600 # 14 days
}

resource "aws_lambda_function" "event_processor" {
  # checkov:skip=CKV_AWS_272: Code signing is deferred until artifact signing is introduced in the CI/CD release process.
  # checkov:skip=CKV_AWS_117: Lambda is intentionally outside VPC because it only calls AWS public control-plane APIs; VPC would add NAT cost/complexity.

  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  function_name    = "${var.prefix}-event-processor"

  role = aws_iam_role.lambda_execution_role.arn

  handler = "handler.lambda_handler"
  runtime = "python3.11"

  memory_size                    = var.memory_size
  timeout                        = var.timeout_seconds
  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = {
      DRY_RUN_MODE            = tostring(var.dry_run_mode)
      SLACK_WEBHOOK_SSM_PARAM = var.slack_webhook_ssm_param
    }
  }

  tracing_config {
    mode = "Active"
  }

  kms_key_arn = var.kms_key_arn

  dead_letter_config {
    target_arn = aws_sqs_queue.lambda_dlq.arn
  }

}

resource "aws_iam_role" "lambda_execution_role" {
  name = "${var.prefix}-lambda-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"

      Principal = {
        Service = "lambda.amazonaws.com"
      }

      Action = "sts:AssumeRole"

    }]
  })
}

resource "aws_iam_role_policy" "xray_tracing" {
  name = "xray-tracing-policy"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords"
        ]
        Effect   = "Allow"
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "lambda_permissions" {
  # checkov:skip=CKV_AWS_355: DescribeInstances/DescribeVolumes do not support resource-level permissions
  name = "${var.prefix}-lambda-permissions"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "WriteCloudWatchLogs"
        Effect = "Allow"

        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]

        Resource = var.cloudwatch_log_group_arn
      },

      {
        Sid    = "ReadEC2State"
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeVolumes",
        ]
        Resource = "*"
      },

      {
        Sid    = "ReadS3State"
        Effect = "Allow"
        Action = [
          "s3:GetBucketPolicy",
          "s3:GetBucketTagging",
        ]
        Resource = "*"
      },

      {
        Sid    = "ReadIAMState"
        Effect = "Allow"
        Action = [
          "iam:ListUserTags",
        ]
        Resource = "*"
      },

      {
        Sid    = "ReadSSMParameter"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
        ]
        Resource = "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${var.slack_webhook_ssm_param}"
      }
    ]
  })
}