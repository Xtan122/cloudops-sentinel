import logging

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

EXEMPTION_TAG_KEY = "exempted-user"


def check_iam_access_key(event_detail: dict, config: dict) -> dict | None:
    """
    Kiem tra CloudTrail CreateAccessKey event theo REQ-4 va REQ-13.

    Mọi IAM access key mới là violation, trừ khi IAM user có tag
    Exempted-User: true.
    """
    # TODO 1: Doc severity tu config["guardrails"]["iam"]["violation_severity"]
    # Fallback severity phai la "high" theo REQ-4.1
    severity = config.get("guardrails", {}).get("iam", {}).get("violation_severity", "high")

    # TODO 2: Parse access_key object tu:
    # event_detail["responseElements"]["accessKey"]
    # Can lay accessKeyId va userName
    try:
        access_key = event_detail["responseElements"]["accessKey"]
        access_key_id = access_key["accessKeyId"]
        username = access_key["userName"]
    except KeyError as e:
        logger.error(f"Malformed CreateAccessKey event, missing key: {e}")
        raise ValueError(f"Malformed CreateAccessKey event, missing key: {e}") from e

    # TODO 3: Goi _is_user_exempted(username)
    # Neu True thi log COMPLIANCE_SKIPPED voi username, access_key_id, reason
    # Sau do return None
    if _is_user_exempted(username):
        logger.info(
            "%s",
            {
                "event": "COMPLIANCE_SKIPPED",
                "resource_id": access_key_id,
                "username": username,
                "access_key_id": access_key_id,
                "reason": f"User has {EXEMPTION_TAG_KEY} tag set to true"
            }
        )
        return None

    # TODO 4: Neu khong exempted, return _create_violation(...)
    return _create_violation(username, access_key_id, severity)


def _is_user_exempted(username: str) -> bool:
    """
    Kiem tra IAM user co tag Exempted-User: true khong theo REQ-4.5.
    """
    iam = boto3.client("iam")

    # TODO 1: Goi iam.list_user_tags(UserName=username)
    # TODO 2: Neu AWS API loi, logger.error(...) voi username roi raise lai
    try:
        response = iam.list_user_tags(UserName=username)
    except ClientError as e:
        logger.error(f"Failed to fetch tags for IAM user {username}: {e}")
        raise

    # TODO 3: Lay response["Tags"], moi tag co shape {"Key": ..., "Value": ...}
    tags = response.get("Tags", [])

    # TODO 4: Return _has_exemption_tag(tags)
    return _has_exemption_tag(tags)


def _has_exemption_tag(tags: list[dict]) -> bool:
    """
    So khop tag Exempted-User: true theo case-insensitive cho safety.
    """
    # TODO: Duyet tags, normalize Key va Value ve lowercase
    # Neu key == "exempted-user" va value == "true" thi return True
    for tag in tags:
        key = tag.get("Key", "").strip().lower()
        value = tag.get("Value", "").strip().lower()
        if key == EXEMPTION_TAG_KEY.lower() and value == "true":
            return True
    return False


def _create_violation(
    username: str,
    access_key_id: str,
    severity: str = "high",
) -> dict:
    """
    Tao violation record theo convention cua guardrail_cost/security.
    """
    # TODO: Return dict gom:
    # violation_type: "iam_access_key_created"
    # resource_type: "iam_access_key"
    # resource_id: access_key_id
    # username: username
    # severity
    # access_key_id
    # message
    return {
        "violation_type": "iam_access_key_created",
        "resource_type": "iam_access_key",
        "resource_id": access_key_id,
        "username": username,
        "severity": severity,
        "access_key_id": access_key_id,
        "message": f"IAM access key {access_key_id} created for user {username}"
    }
