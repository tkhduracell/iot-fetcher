import logging

from tapo_cloud import tapo as tapo_cloud
from tapo_local import tapo as tapo_local

# Configure module-specific logger
logger = logging.getLogger(__name__)


def tapo():
    """
    Main TAPO integration entry point.

    Runs both cloud and local discovery methods:
    - Cloud discovery: Gets device inventory from TP-Link cloud (tapo_cloud_* metrics)
    - Local discovery: Scans local network for devices with energy monitoring (tapo_* metrics)
    """
    logger.info("[tapo] Running TAPO integration (cloud + local discovery)")

    # Run cloud-based discovery
    try:
        logger.debug("[tapo] Starting cloud discovery...")
        tapo_cloud()
    except Exception as e:
        logger.error(f"[tapo] Cloud discovery failed: {e}")

    # Run local network discovery
    try:
        logger.debug("[tapo] Starting local discovery...")
        tapo_local()
    except Exception as e:
        logger.error(f"[tapo] Local discovery failed: {e}")

    logger.info("[tapo] TAPO integration completed")
