import logging
import os

# Configure module-specific logger
logger = logging.getLogger(__name__)


def strip_quote(s: str) -> str:
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


tapo_email = strip_quote(os.environ.get('TAPO_EMAIL', ''))
tapo_password = strip_quote(os.environ.get('TAPO_PASSWORD', ''))


def has_credentials() -> bool:
    if not tapo_email or not tapo_password:
        logger.error(
            "[tapo] TAPO_EMAIL and TAPO_PASSWORD environment variables must be set")
        return False
    return True
