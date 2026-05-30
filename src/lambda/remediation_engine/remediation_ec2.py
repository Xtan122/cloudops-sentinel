import logging

import boto3
import botocore.exceptions

logger = logging.getLogger(__name__)


def stop_non_compliant_ec2(instance_id: str, region: str, dry_run: bool) -> dict:
    """
    Stop EC2 instance vi phạm tagging policy theo REQ-2.5 và REQ-2.6.
    """
    action = {
        "action": "stop_ec2",
        "resource_type": "ec2",
        "resource_id": instance_id,
        "region": region,
        "dry_run": dry_run,
    }

    if dry_run:
        logger.info("[DRY-RUN] Would stop EC2 instance %s in %s", instance_id, region)
        action.update({"executed": False, "reason": "dry_run_mode", "status": "skipped"})
        return action

    ec2 = boto3.client("ec2", region_name=region)

    try:
        ec2.stop_instances(InstanceIds=[instance_id])
        logger.info(
            "remediation_executed",
            extra={
                "resource_type": "ec2",
                "resource_id": instance_id,
                "region": region,
                "action": "stop_ec2"
            }
        )
        action.update({"executed": True, "status": "stop_requested"})
        return action
    except botocore.exceptions.ClientError as exc:
        logger.error("Failed to stop EC2 instance %s in %s: %s", instance_id, region, exc, exc_info=True)
        raise