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