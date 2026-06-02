import json
import logging

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)

BEDROCK_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"
BEDROCK_REGION = "us-east-1"
MAX_TOKENS = 512


def generate_report(violation: dict) -> str:
    """Tạo Slack markdown report từ violation dict."""
    fallback_report = _generate_template_report(violation)

    try:
        ai_report = _call_bedrock(violation)
        if not ai_report or not ai_report.strip():
            return fallback_report
        return ai_report.strip()
    except Exception as e:
        logger.warning("Bedrock generation failed, falling back to template: %s", e)
        return fallback_report


def _build_prompt(violation: dict) -> str:
    """Xây prompt ngắn gọn cho Claude 3 Haiku."""
    violation_type = violation.get("violation_type", "unknown")
    resource_type = violation.get("resource_type", "unknown")
    resource_id = violation.get("resource_id", "unknown")
    severity = violation.get("severity", "unknown")
    message = violation.get("message", "unknown")
    owner = violation.get("owner", "unknown")
    region = violation.get("region", "unknown")

    prompt = (
        f"ROLE: You are a senior CloudOps assistant with 15 years of experience.\n"
        f"AUDIENCE: Cloud Operations, Security, and FinOps Engineers reading Slack.\n"
        f"CONTEXT: A compliance violation has been detected in the AWS environment by the CloudOps Sentinel system.\n"
        f"TASK: Generate a short, factual Slack markdown report for the compliance violation. "
        f"Include the violation type, resource details ({resource_type}: {resource_id}), owner ({owner}), region ({region}), and recommend a safe next step based only on provided details.\n"
        f"CONSTRAINT: Output ONLY the Slack markdown report. Do not add conversational filler. Write the report in Vietnamese.\n\n"
        f"Violation Details:\n"
        f"Type: {violation_type}\n"
        f"Severity: {severity}\n"
        f"Message: {message}\n"
    )

    if "cost" in violation_type.lower() or "ec2" in resource_type.lower():
        prompt += "\nADDITIONAL CONSTRAINT: This is a cost-related violation. Please only describe the qualitative cost impact. Do NOT invent specific dollar amounts, percentages, or concrete estimates since pricing data is not provided in the input.\n"

    return prompt


def _call_bedrock(violation: dict) -> str:
    """Call Amazon Bedrock Claude 3 Haiku."""
    config = Config(connect_timeout=0.5, read_timeout=2.0, retries={"mode": "standard", "max_attempts": 1})
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION, config=config)
    prompt = _build_prompt(violation)

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt}
                ]
            }
        ]
    }

    response = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(body)
    )

    response_body = json.loads(response.get("body").read())
    return response_body["content"][0]["text"]


def _generate_template_report(violation: dict) -> str:
    """Fallback template khi Bedrock lỗi theo REQ-6.5."""
    severity = violation.get("severity", "unknown").upper()
    resource_id = violation.get("resource_id", "unknown")
    violation_type = violation.get("violation_type", "unknown")
    message = violation.get("message", "unknown")
    region = violation.get("region", "unknown")

    if resource_id == "unknown" or violation_type == "unknown":
        logger.warning("Missing critical fields in violation. Resource ID: %s, Violation Type: %s", resource_id, violation_type)

    severity_icon_map = {
        "CRITICAL": "🔴",
        "HIGH": "🟠",
        "MEDIUM": "🟡",
        "LOW": "🟢",
        "UNKNOWN": "⚪"
    }
    icon = severity_icon_map.get(severity, "⚪")

    report = f"*{icon} Vi Phạm Tuân Thủ: {violation_type}*\n"
    report += f"• *Mức độ:* {severity}\n"
    report += f"• *Tài nguyên:* `{resource_id}` ({region})\n"
    report += f"• *Chi tiết:* {message}\n"
    report += "_(Báo cáo tự động được tạo qua template)_"

    return report
