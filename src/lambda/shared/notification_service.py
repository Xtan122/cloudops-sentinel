import json
import logging
import os
import time
from datetime import datetime

import urllib3

logger = logging.getLogger(__name__)

SEVERITY_COLORS = {
    "critical": "#FF0000",
    "high": "#FF6600",
    "medium": "#FFCC00",
    "low": "#36A64F",
}

DEFAULT_SEVERITY = "medium"
DEFAULT_REGION = "unknown"


def send_violation_alert(violation: dict, ai_report: str, dry_run: bool) -> bool:
    """
    Gửi Slack alert cho violation.

    Bám requirements:
    - REQ-11.1: gửi Slack qua webhook
    - REQ-11.2: attachment color theo severity
    - REQ-8.3: thêm [DRY-RUN] prefix khi dry_run=True
    - REQ-11.5: fail thì log và tiếp tục
    """
    severity = str(violation.get("severity", DEFAULT_SEVERITY)).lower()
    color = SEVERITY_COLORS.get(severity, SEVERITY_COLORS[DEFAULT_SEVERITY])
    prefix = "[DRY-RUN] " if dry_run else ""

    attachment = {
        "color": color,
        "title": f"{prefix}CloudOps Sentinel Alert",
        "text": ai_report,
        "footer": (
            f"Resource: {violation.get('resource_id', 'unknown')}"
            f" | Region: {violation.get('region', DEFAULT_REGION)}"
        ),
    }

    # TODO 1: Nếu violation có timestamp phù hợp Slack ts, gắn vào đây
    if "timestamp" in violation:
        ts_val = violation["timestamp"]
        try:
            if isinstance(ts_val, str):
                ts_val = ts_val.replace("Z", "+00:00")
                attachment["ts"] = int(datetime.fromisoformat(ts_val).timestamp())
            elif isinstance(ts_val, (int, float)):
                attachment["ts"] = int(ts_val)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Could not parse timestamp %r for Slack alert: %s",
                ts_val,
                exc,
            )

    payload = {
        "attachments": [attachment]
    }

    # TODO 2: Gọi _send_with_retry(payload) và return kết quả bool
    return _send_with_retry(payload)


def _send_with_retry(payload: dict, max_retries: int = 3) -> bool:
    """
    Gửi payload đến Slack webhook với retry exponential backoff.

    Bám requirements:
    - REQ-11.4: retry tối đa 3 lần với exponential backoff
    - REQ-11.5: nếu thất bại hết thì log error và tiếp tục
    """
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")

    # TODO 3: Nếu thiếu webhook_url:
    if not webhook_url:
        logger.error("Missing SLACK_WEBHOOK_URL environment variable")
        return False

    http = urllib3.PoolManager()

    for attempt in range(max_retries):
        try:
            # TODO 4: Gửi POST request:
            response = http.request(
                "POST",
                webhook_url,
                body=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=3.0
            )

            # TODO 5: Nếu response.status trong khoảng 200-299:
            if 200 <= response.status < 300:
                return True

            # TODO 6: Nếu status không thành công:
            logger.warning(
                "Slack webhook attempt %s failed with status %s",
                attempt + 1,
                response.status,
            )

        except Exception as exc:
            # TODO 7: logger.warning(...) với attempt và exc
            logger.warning(
                "Slack webhook attempt %s failed with exception: %s",
                attempt + 1,
                exc,
            )

        # TODO 8: Nếu chưa phải lần retry cuối:
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)

    # TODO 9: logger.error(...) sau khi hết retries
    logger.error("Failed to send Slack alert after %s attempts", max_retries)
    
    # TODO 10: return False
    return False
