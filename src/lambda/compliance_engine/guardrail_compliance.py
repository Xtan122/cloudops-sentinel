import logging

import boto3

from shared.exclusion_checker import EXCLUSION_TAG_KEY, has_exclusion_tag

logger = logging.getLogger(__name__)


def check_ebs_encryption(volume_id: str, region: str, config: dict) -> dict | None:
    """Kiem tra EBS volume co encrypted theo REQ-5 va REQ-9."""
    ec2 = boto3.client("ec2", region_name=region)

    severity = config.get("guardrails", {}).get("compliance", {}).get("violation_severity", "high")

  
    try:
        response = ec2.describe_volumes(VolumeIds=[volume_id])
    except Exception as exc:
        logger.error(
            "AWS API error while describing EBS volume — "
            "resource_id=%s region=%s error=%s",
            volume_id, region, exc,
        )
        raise


    volumes = response.get("Volumes", [])
    if not volumes:
        logger.error(
            "EBS volume not found in describe_volumes response — "
            "resource_id=%s region=%s — cannot verify encryption, raising to caller",
            volume_id, region,
        )
        raise ValueError(
            f"EBS volume {volume_id!r} not found in describe_volumes response "
            f"(region={region})"
        )

    volume = volumes[0]

    tags = volume.get("Tags", [])
    if has_exclusion_tag(tags):
        logger.info(
            "%s",
            {
                "event": "COMPLIANCE_SKIPPED",
                "reason": "exclusion_tag_present",
                "resource_id": volume_id,
                "region": region,
                "tag": EXCLUSION_TAG_KEY,
            },
        )
        return None

    encrypted = volume.get("Encrypted")
    if encrypted is True:
        return None
    elif encrypted is False:
        return _create_violation(
            volume_id=volume_id,
            region=region,
            severity=severity,
        )
    else:
        logger.error(
            "EBS volume missing 'Encrypted' state in response — "
            "resource_id=%s region=%s",
            volume_id, region,
        )
        raise ValueError(
            f"EBS volume {volume_id!r} missing 'Encrypted' state "
            f"(region={region})"
        )




def _create_violation(volume_id: str, region: str, severity: str = "high") -> dict:
    # TODO: Return dict gom:
    # violation_type: "unencrypted_ebs_volume"
    # resource_type: "ebs"
    # resource_id: volume_id
    # region
    # severity
    # message
    return {
        "violation_type": "unencrypted_ebs_volume",
        "resource_type": "ebs",
        "resource_id": volume_id,
        "region": region,
        "severity": severity,
        "message": f"EBS volume {volume_id} is not encrypted.",
    }
