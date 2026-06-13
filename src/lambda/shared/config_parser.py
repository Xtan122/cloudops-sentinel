import json
import logging
import os
import copy
from jsonschema import ValidationError, validate

from shared.dry_run import get_dry_run_mode

logger = logging.getLogger(__name__)

COST_GUARDRAIL_SCHEMA = {
    "type": "object",
    "required": ["enabled", 
                 "violation_severity", 
                 "required_tags", 
                 "remediation_delay_minutes"],
    "properties": {
        "enabled": {"type": "boolean"},
        "required_tags": {
            "type": "array",
            "items": {"type": "string"}
        },

        "violation_severity": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"]
        },

        "remediation_delay_minutes": {"type": "integer", "minimum": 0}
    },

    "additionalProperties": False
}
SECURITY_GUARDRAIL_SCHEMA = {
    "type": "object",
    "required": ["enabled", 
                 "violation_severity",
                 "remediation_delay_seconds"],
    "properties": {
        "enabled": {"type": "boolean"},
        "violation_severity": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"]
        },
        "remediation_delay_seconds": {"type": "integer", "minimum": 0}
    },

    "additionalProperties": False
}
IAM_GUARDRAIL_SCHEMA = {
    "type": "object",
    "required": ["enabled", 
                 "violation_severity",
                 "remediation_delay_seconds"],
    "properties": {
        "enabled": {"type": "boolean"},
        "violation_severity": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"]
        },
        "remediation_delay_seconds": {"type": "integer", "minimum": 0}
    },

    "additionalProperties": False
}
COMPLIANCE_GUARDRAIL_SCHEMA = {
    "type": "object",
    "required": ["enabled", 
                 "violation_severity"],
    "properties": {
        "enabled": {"type": "boolean"},
        "violation_severity": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"]
        }
    },

    "additionalProperties": False
}
CONFIG_SCHEMA = {
    "type": "object",
    "required": ["version", "guardrails"],
    "properties": {
        "version": {"type": "string"},
        "dry_run_mode": {"type": "boolean"},
        "guardrails": {
            "type": "object",
            "required": ["cost", "security", "iam", "compliance"],
            "properties": {
                "cost": COST_GUARDRAIL_SCHEMA,
                "security": SECURITY_GUARDRAIL_SCHEMA,
                "iam": IAM_GUARDRAIL_SCHEMA,
                "compliance": COMPLIANCE_GUARDRAIL_SCHEMA
            },
            "additionalProperties": False 
        },
        "slack": {
            "type": "object",
            "required": ["webhook_url_env", "channel"],
            "properties": {
                "webhook_url_env": {"type": "string"},
                "channel": {"type": "string"},
                "timezone": {"type": "string"}
            },
            "additionalProperties": False
        },
        "logging": {
            "type": "object",
            "properties": {
                "level": {"type": "string"},
                "cloudwatch_retention_days": {"type": "integer"}
            },
            "additionalProperties": False
        }
    },

    "additionalProperties": False
}

# Cấu trúc fallback chuẩn xác, bọc lót đầy đủ các nhánh để downstream code không bị lỗi KeyError
MINIMAL_SAFE_CONFIG = {
    "version": "unknown",
    "dry_run_mode": True,
    "guardrails": {
        "cost": {"enabled": False, "violation_severity": "medium", "required_tags": [], "remediation_delay_minutes": 0},
        "security": {"enabled": False, "violation_severity": "critical", "remediation_delay_seconds": 0},
        "iam": {"enabled": False, "violation_severity": "high", "remediation_delay_seconds": 0},
        "compliance": {"enabled": False, "violation_severity": "high"}
    },
    "slack": {
        "webhook_url_env": "SLACK_WEBHOOK_SSM_PARAM",
        "channel": "#cloud-alerts",
        "timezone": "UTC"
    },
    "logging": {
        "level": "INFO",
        "cloudwatch_retention_days": 90
    }
}



def load_config(config_path: str) -> dict:
    """Load JSON config, validate schema, áp dụng fail-safe và env override."""
    raw_config = {}
    
    # 1. Đọc file JSON
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw_config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.error("Error reading config file: %s. Loading minimal safe config.", exc)
        return copy.deepcopy(MINIMAL_SAFE_CONFIG)

    # 2. Validate cấu trúc file qua JSON Schema
    try:
        validate(instance=raw_config, schema=CONFIG_SCHEMA)
    except ValidationError as exc:
        logger.error("Config validation error: %s. Loading minimal safe config.", exc)
        return copy.deepcopy(MINIMAL_SAFE_CONFIG)
    
    # 3. Điền giá trị mặc định cho dry_run_mode nếu thiếu trong file
    if "dry_run_mode" not in raw_config:
        raw_config["dry_run_mode"] = True

    # 4. Xử lý Environment Overrides với cơ chế Fail-Safe tuyệt đối
    file_default = raw_config.get("dry_run_mode", True)
    final_dry_run = get_dry_run_mode(default=file_default)
    raw_config["dry_run_mode"] = final_dry_run
    logger.info("DRY_RUN_MODE resolved to: %s (file_default=%s)", final_dry_run, file_default)

    return raw_config