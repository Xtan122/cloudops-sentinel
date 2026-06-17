output "function_name" {
  description = "Event Processor Lambda name"
  value       = aws_lambda_function.event_processor.function_name
}

output "function_arn" {
  description = "ARN used as EventBridge target"
  value       = aws_lambda_function.event_processor.arn
}

output "execution_role_arn" {
  description = "ARN of Lambda execution role"
  value       = aws_iam_role.lambda_execution_role.arn
}

output "approval_handler_function_name" {
  description = "Approval Handler Lambda name"
  value       = aws_lambda_function.approval_handler.function_name
}

output "approval_callback_url" {
  description = "Slack Interactivity callback URL for approval actions"
  value       = "${trimsuffix(aws_apigatewayv2_stage.approval_default.invoke_url, "/")}/slack/approval"
}

output "approval_table_name" {
  description = "DynamoDB table storing approval requests"
  value       = aws_dynamodb_table.approval_requests.name
}
