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
      CONFIG_PATH             = "config/guardrails.json"
      APPROVAL_TABLE_NAME     = aws_dynamodb_table.approval_requests.name
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

resource "aws_lambda_function" "approval_handler" {
  # checkov:skip=CKV_AWS_272: Code signing is deferred until artifact signing is introduced in the CI/CD release process.
  # checkov:skip=CKV_AWS_117: Lambda is intentionally outside VPC because it only calls AWS public control-plane APIs; VPC would add NAT cost/complexity.

  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  function_name    = "${var.prefix}-approval-handler"

  role = aws_iam_role.lambda_execution_role.arn

  handler = "shared.approval_handler.lambda_handler"
  runtime = "python3.11"

  memory_size                    = var.memory_size
  timeout                        = var.timeout_seconds
  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = {
      DRY_RUN_MODE            = tostring(var.dry_run_mode)
      CONFIG_PATH             = "config/guardrails.json"
      APPROVAL_TABLE_NAME     = aws_dynamodb_table.approval_requests.name
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

resource "aws_cloudwatch_log_group" "approval_handler_logs" {
  # checkov:skip=CKV_AWS_338: REQ-12.6 requires 90-day retention; 1-year retention is intentionally deferred for cost control per REQ-18.3.
  name              = "/aws/lambda/${var.prefix}-approval-handler"
  retention_in_days = 90
  kms_key_id        = var.kms_key_arn
}

resource "aws_dynamodb_table" "approval_requests" {
  name         = "${var.prefix}-approval-requests"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "request_id"

  attribute {
    name = "request_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }
}

resource "aws_apigatewayv2_api" "approval" {
  name          = "${var.prefix}-approval-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "approval_handler" {
  api_id                 = aws_apigatewayv2_api.approval.id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = aws_lambda_function.approval_handler.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "slack_approval" {
  # checkov:skip=CKV_AWS_309: Slack Interactivity requires a public callback URL; request authenticity is handled at the application boundary.
  api_id    = aws_apigatewayv2_api.approval.id
  route_key = "POST /slack/approval"
  target    = "integrations/${aws_apigatewayv2_integration.approval_handler.id}"
}

resource "aws_apigatewayv2_stage" "approval_default" {
  # checkov:skip=CKV_AWS_76: Access logging is deferred for the dev callback API; Lambda approval logs retain request/audit context.
  api_id      = aws_apigatewayv2_api.approval.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "allow_approval_api" {
  statement_id  = "AllowExecutionFromApprovalApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.approval_handler.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.approval.execution_arn}/*/*"
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

        Resource = [
          "${var.cloudwatch_log_group_arn}:*",
          "${aws_cloudwatch_log_group.approval_handler_logs.arn}:*"
        ]
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
      },

      {
        Sid    = "WriteToDLQ"
        Effect = "Allow"
        Action = [
          "sqs:SendMessage"
        ]
        Resource = aws_sqs_queue.lambda_dlq.arn
      },

      {
        Sid    = "ApprovalTableAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ]
        Resource = aws_dynamodb_table.approval_requests.arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "remediation_permissions" {
  # checkov:skip=CKV_AWS_287: IAM UpdateAccessKey is the required remediation action for REQ-4 and is limited to IAM users in this account.
  # checkov:skip=CKV_AWS_289: Remediation permissions intentionally modify governed resources; the action list is constrained to supported guardrails.
  # checkov:skip=CKV_AWS_290: Write permissions are required for remediation; dry-run and Slack approval are the safety controls.
  # checkov:skip=CKV_AWS_355: Some remediation APIs require broad resources or dynamic targets discovered from events.
  name = "${var.prefix}-remediation-permissions"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EC2Remediation"
        Effect = "Allow"
        Action = [
          "ec2:StopInstances",
          "ec2:CreateTags"
        ]
        Resource = "*"
      },
      {
        Sid    = "S3Remediation"
        Effect = "Allow"
        Action = [
          "s3:DeleteBucketPolicy",
          "s3:PutBucketPolicy"
        ]
        Resource = "*"
      },
      {
        Sid    = "IAMRemediation"
        Effect = "Allow"
        Action = [
          "iam:UpdateAccessKey"
        ]
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/*"
      },
      {
        Sid    = "BedrockAIReport"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel"
        ]
        Resource = "*"
      }
    ]
  })
}
