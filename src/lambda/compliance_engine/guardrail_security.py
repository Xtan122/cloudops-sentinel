import json
import logging

import boto3
import botocore.exceptions

logger = logging.getLogger(__name__)

EXCLUSION_TAG_KEY = "skip-enforcement"


def check_s3_public_access(bucket_name: str, region: str, config: dict) -> dict | None:
    """Kiem tra S3 bucket co public read/write policy theo REQ-3 va REQ-9."""
    s3 = boto3.client("s3", region_name=region)

    # TODO 1: Doc severity tu config["guardrails"]["security"]["violation_severity"]
    # Fallback nen la "critical" theo REQ-3.2 va REQ-3.3
    severity = config.get("guardrails", {}).get("security", {}).get("violation_severity", "critical")

    # TODO 2: Goi get_bucket_tagging() de lay tags cua bucket
    # Neu bucket khong co tag set, coi nhu tags = []
    # Neu AWS API loi khac, log context roi raise lai
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

    # TODO 3: Kiem tra _has_exclusion_tag(tags)
    # Neu co Skip-Enforcement: true thi log COMPLIANCE_SKIPPED va return None
    if _has_exclusion_tag(tags):
        logger.info(
            json.dumps(
                {
                    "event": "COMPLIANCE_SKIPPED",
                    "reason": "excluded via tags",
                    "resource_id": bucket_name,
                    "region": region,
                }
            )
        )
        return None

    # TODO 4: Goi get_bucket_policy(Bucket=bucket_name)
    # Neu bucket khong co policy thi return None
    # Neu AWS API loi khac, log context roi raise lai
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

    # TODO 5: Lay policy string tu response["Policy"]
    # Goi is_policy_public(policy_json)
    is_public, access_type = is_policy_public(policy_json)

    # TODO 6: Neu public thi return _create_violation(...)
    # Neu khong public thi return None
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
    # TODO 1: json.loads(policy_json)
    try:
        policy = json.loads(policy_json)
    except json.JSONDecodeError as e:
        logger.error(f"Malformed bucket policy: {e}")
        # Chuyen thanh loi de bao viec kiem tra that bai thay vi bao cao compliant
        raise ValueError("Malformed bucket policy JSON") from e

    # TODO 2: Duyet tung statement trong policy["Statement"]
    # Luu y: Statement co the la dict hoac list
    statements = _normalize_to_list(policy.get("Statement", []))

    has_read = False
    has_write = False

    for statement in statements:
        # TODO 3: Chi xu ly statement co Effect == "Allow"
        if statement.get("Effect") != "Allow":
            continue

        # TODO 4: Kiem tra Principal public
        principal = statement.get("Principal")
        if not _is_public_principal(principal):
            continue

        # TODO 5: Normalize Action thanh list
        actions = _normalize_to_list(statement.get("Action", []))

        # TODO 6: Kiem tra action
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


def _has_exclusion_tag(tags: list[dict]) -> bool:
    # TODO: Giong Bài 2.1, so khop key/value case-insensitive cho Skip-Enforcement: true
    for tag in tags:
        key = tag.get("Key", "")
        value = tag.get("Value", "")
        if key.lower() == EXCLUSION_TAG_KEY.lower() and value.lower() == "true":
            return True
    return False


def _create_violation(
    bucket_name: str,
    region: str,
    access_type: str,
    severity: str = "critical",
) -> dict:
    # TODO: Return violation record gom:
    # violation_type, resource_type, resource_id, region, severity, access_type, message
    return {
        "violation_type": "public_s3_access",
        "resource_type": "s3",
        "resource_id": bucket_name,
        "region": region,
        "severity": severity,
        "access_type": access_type,
        "message": f"S3 Bucket {bucket_name} allows public {access_type} access",
    }
