# REQ-1.1, REQ-14, REQ-18.4
resource "aws_cloudwatch_event_rule" "ec2_state_change" {
  name        = "${var.prefix}-ec2-state-change"
  description = "Capture EC2 instances entering running state"

  event_pattern = jsonencode({
    source      = ["aws.ec2"],
    detail-type = ["EC2 Instance State-change Notification"],
    detail = {
      state = ["running"]
    }
  })
}

resource "aws_cloudwatch_event_target" "ec2_state_change_to_lambda" {
  rule      = aws_cloudwatch_event_rule.ec2_state_change.name
  target_id = "SendToLambda"
  arn       = var.lambda_function_arn
}

resource "aws_lambda_permission" "allow_ec2_state_change" {
  statement_id  = "AllowExecutionFromEventBridgeEC2StateChange"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_function_arn
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ec2_state_change.arn
}

# --------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "s3_put_bucket_policy" {
  name        = "${var.prefix}-s3-put-bucket-policy"
  description = "Capture S3 PutBucketPolicy API calls"
  event_pattern = jsonencode({
    source      = ["aws.s3"],
    detail-type = ["AWS API Call via CloudTrail"],
    detail = {
      eventName = ["PutBucketPolicy"]
    }
  })
}

resource "aws_cloudwatch_event_target" "s3_put_bucket_policy_to_lambda" {
  rule      = aws_cloudwatch_event_rule.s3_put_bucket_policy.name
  target_id = "SendToLambda"
  arn       = var.lambda_function_arn
}

resource "aws_lambda_permission" "allow_s3_put_bucket_policy" {
  statement_id  = "AllowExecutionFromEventBridgeS3PutBucketPolicy"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_function_arn
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.s3_put_bucket_policy.arn
}

# --------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "iam_create_access_key" {
  name        = "${var.prefix}-iam-create-access-key"
  description = "Capture IAM CreateAccessKey API calls"
  event_pattern = jsonencode({
    source      = ["aws.iam"],
    detail-type = ["AWS API Call via CloudTrail"],
    detail = {
      eventName = ["CreateAccessKey"]
    }
  })
}

resource "aws_cloudwatch_event_target" "iam_create_access_key_to_lambda" {
  rule      = aws_cloudwatch_event_rule.iam_create_access_key.name
  target_id = "SendToLambda"
  arn       = var.lambda_function_arn
}

resource "aws_lambda_permission" "allow_iam_create_access_key" {
  statement_id  = "AllowExecutionFromEventBridgeIAMCreateAccessKey"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_function_arn
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.iam_create_access_key.arn
}

# --------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "ebs_create_volume" {
  name        = "${var.prefix}-ebs-create-volume"
  description = "Capture EBS CreateVolume API calls"
  event_pattern = jsonencode({
    source      = ["aws.ec2"],
    detail-type = ["AWS API Call via CloudTrail"],
    detail = {
      eventName = ["CreateVolume"]
    }
  })
}

resource "aws_cloudwatch_event_target" "ebs_create_volume_to_lambda" {
  rule      = aws_cloudwatch_event_rule.ebs_create_volume.name
  target_id = "SendToLambda"
  arn       = var.lambda_function_arn
}

resource "aws_lambda_permission" "allow_ebs_create_volume" {
  statement_id  = "AllowExecutionFromEventBridgeEBSCreateVolume"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_function_arn
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ebs_create_volume.arn
}