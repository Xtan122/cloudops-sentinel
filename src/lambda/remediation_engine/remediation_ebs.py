import logging

import boto3
import botocore.exceptions

logger = logging.getLogger(__name__)


def tag_non_compliant_ebs(volume_id: str, region: str, dry_run: bool) -> dict:
    """Tag EBS volume không mã hóa là Non-Compliant theo REQ-5.3 và REQ-5.5."""
    action = {
        "action": "tag_ebs_non_compliant",
        "resource_type": "ebs",
        "resource_id": volume_id,
        "region": region,
        "dry_run": dry_run,
    }

    if dry_run:
        logger.info(
            "[DRY-RUN] Would tag EBS volume %s in region %s as Non-Compliant",
            volume_id,
            region,
        )
        action.update({
            "executed": False,
            "reason": "dry_run_mode",
            "status": "skipped",
        })
        return action

    ec2 = boto3.client("ec2", region_name=region)

    try:
        ec2.create_tags(
            Resources=[volume_id],
            Tags=[{"Key": "Compliance-Status", "Value": "Non-Compliant"}],
        )
        
        action.update({
            "executed": True,
            "status": "ebs_tagged_non_compliant",
        })
        return action

    except botocore.exceptions.ClientError as exc:
        logger.error(
            "Error tagging EBS volume %s in region %s: %s",
            volume_id,
            region,
            exc,
        )
        raise
