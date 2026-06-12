import logging

import boto3

from shared.exclusion_checker import EXCLUSION_TAG_KEY, has_exclusion_tag

logger = logging.getLogger(__name__)

DEFAULT_REQUIRED_TAGS = ["Owner", "Project"]


def check_ec2_tagging(instance_id: str, region: str, config: dict) -> dict | None:
    """Kiem tra EC2 instance co du required tags theo REQ-2 va REQ-9."""
    ec2 = boto3.client("ec2", region_name=region)

    # REQ-10.4: doc required_tags va severity tu config, fallback ve gia tri mac dinh
    cost_cfg: dict = config.get("guardrails", {}).get("cost", {})
    required_tags: list[str] = cost_cfg.get("required_tags", DEFAULT_REQUIRED_TAGS)
    severity: str = cost_cfg.get("violation_severity", "medium")

    # REQ-13.1: AWS API loi phai log day du context roi raise lai
    try:
        response = ec2.describe_instances(InstanceIds=[instance_id])
    except Exception as exc:
        logger.error(
            "AWS API error while describing EC2 instance — "
            "resource_id=%s region=%s error=%s",
            instance_id, region, exc,
        )
        raise

    # Trang thai bat thuong: instance khong ton tai trong response
    reservations = response.get("Reservations", [])
    instances = reservations[0].get("Instances", []) if reservations else []
    if not instances:
        logger.error(
            "EC2 instance not found in describe_instances response — "
            "resource_id=%s region=%s — cannot verify tags, raising to caller",
            instance_id, region,
        )
        raise ValueError(
            f"EC2 instance {instance_id!r} not found in describe_instances response "
            f"(region={region})"
        )

    tags: list[dict] = instances[0].get("Tags", [])

    # REQ-9 + REQ-12: skip voi structured log
    if has_exclusion_tag(tags):
        logger.info(
            "%s",
            {
                "event": "COMPLIANCE_SKIPPED",
                "reason": "exclusion_tag_present",
                "resource_id": instance_id,
                "region": region,
                "tag": EXCLUSION_TAG_KEY,
            },
        )
        return None

    existing_tag_keys: set[str] = {tag.get("Key", "") for tag in tags}
    missing_tags: list[str] = [t for t in required_tags if t not in existing_tag_keys]

    if not missing_tags:
        return None

    return _create_violation(
        instance_id=instance_id,
        missing_tags=missing_tags,
        region=region,
        severity=severity,
    )




def _create_violation(
    instance_id: str,
    missing_tags: list,
    region: str,
    severity: str = "medium",
) -> dict:

    return {
        "violation_type": "missing_required_tags",   # snake_case: convention thong nhat
        "resource_type": "ec2",
        "resource_id": instance_id,
        "region": region,
        "severity": severity,
        "missing_tags": missing_tags,
        "message": (
            f"EC2 instance {instance_id} is missing required tags: "
            f"{', '.join(missing_tags)}."
        ),
    }
