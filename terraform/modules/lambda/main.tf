data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_lambda_function" "event_processor" {
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
  name = "${var.prefix}-lambda-permissions"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "WriteCloudWatchLogs"
        Effect = "Allow"

        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]

        Resource = "*"
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