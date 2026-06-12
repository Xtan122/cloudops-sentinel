import logging
import os

logger = logging.getLogger(__name__)

TRUE_VALUES = {"true", "1", "yes"}
FALSE_VALUES = {"false", "0", "no"}

def get_dry_run_mode(default: bool = True) -> bool:
    """
    Parse DRY_RUN_MODE từ env theo REQ-8.1, REQ-8.4, REQ-8.5.
    """
    env_val = os.environ.get("DRY_RUN_MODE")
    if not env_val or not env_val.strip():
        return default
        
    normalized = env_val.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    elif normalized in FALSE_VALUES:
        return False
    else:
        logger.warning("Invalid DRY_RUN_MODE env var: %s. Forcing dry_run_mode=True as fail-safe.", env_val)
        return True