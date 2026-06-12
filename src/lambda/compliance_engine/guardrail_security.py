import json
import logging

import boto3
import botocore.exceptions

from shared.exclusion_checker import EXCLUSION_TAG_KEY, has_exclusion_tag

logger = logging.getLogger(__name__)

def check_s3_public_access(bucket_name: str, region: str, config: dict) -> dict | None:
    """Kiem tra S3 bucket co public read/write policy theo REQ-3 va REQ-9."""
    s3 = boto3.client("s3", region_name=region)

    severity = config.get("guardrails", {}).get("security", {}).get("violation_severity", "critical")

    tags = []
    try:
        response = s3.get_bucket_tagging(Bucket=bucket_name)
        tags = response.get("TagSet", [])
    except botocore.exceptions.ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "NoSuchTagSet":
            tags = []
        else:
            logger.error(f"Error getting tags for bucket {bucket_name}: {e}")
            raise

    if has_exclusion_tag(tags):
        logger.info(
            json.dumps(
                {
                    "event": "COMPLIANCE_SKIPPED",
                    "reason": "exclusion_tag_present",
                    "resource_id": bucket_name,
                    "region": region,
                    "tag": EXCLUSION_TAG_KEY,
                }
            )
        )
        return None


    try:
        response = s3.get_bucket_policy(Bucket=bucket_name)
        policy_json = response.get("Policy", "{}")
    except botocore.exceptions.ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "NoSuchBucketPolicy":
            return None
        else:
            logger.error(f"Error getting policy for bucket {bucket_name}: {e}")
            raise


    is_public, access_type = is_policy_public(policy_json)


    if is_public:
        return _create_violation(
            bucket_name=bucket_name,
            region=region,
            access_type=access_type,
            severity=severity,
        )
    return None


def _normalize_to_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _is_public_principal(principal) -> bool:
    if principal == "*":
        return True
    if isinstance(principal, dict):
        aws_principal = principal.get("AWS")
        if aws_principal == "*":
            return True
        if isinstance(aws_principal, list) and "*" in aws_principal:
            return True
    return False


def is_policy_public(policy_json: str) -> tuple[bool, str]:
    """
    Parse S3 bucket policy va kiem tra public access.
    Returns: (is_public, access_type) voi access_type la "read", "write", "read_write" hoac "none".
    """
    try:
        policy = json.loads(policy_json)
    except json.JSONDecodeError as e:
        logger.error(f"Malformed bucket policy: {e}")
        # Chuyen thanh loi de bao viec kiem tra that bai thay vi bao cao compliant
        raise ValueError("Malformed bucket policy JSON") from e


    statements = _normalize_to_list(policy.get("Statement", []))

    has_read = False
    has_write = False

    for statement in statements:
        if statement.get("Effect") != "Allow":
            continue

        principal = statement.get("Principal")
        if not _is_public_principal(principal):
            continue

        actions = _normalize_to_list(statement.get("Action", []))

        for action in actions:
            action_lower = str(action).lower()
            if action_lower == "s3:*":
                has_read = True
                has_write = True
            elif action_lower.startswith("s3:put"):
                has_write = True
            elif action_lower.startswith("s3:get"):
                has_read = True

    if has_read and has_write:
        return True, "read_write"
    if has_write:
        return True, "write"
    if has_read:
        return True, "read"
    return False, "none"




def _create_violation(
    bucket_name: str,
    region: str,
    access_type: str,
    severity: str = "critical",
) -> dict:

    return {
        "violation_type": "public_s3_access",
        "resource_type": "s3",
        "resource_id": bucket_name,
        "region": region,
        "severity": severity,
        "access_type": access_type,
        "message": f"S3 Bucket {bucket_name} allows public {access_type} access",
    }
