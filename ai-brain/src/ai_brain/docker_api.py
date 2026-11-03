"""Docker API URLs: the one place a container name becomes part of one.

Below both ``executors`` and ``tools`` so a read tool can build a URL without
importing "the only code that changes something in the house". Building the
URL here rather than validating at each caller means a new ``/containers/...``
call cannot forget the check.
"""

from __future__ import annotations

import re

from ai_brain.errors import BadInput

# Docker's own rule for a container name (it also matches an id). The name is
# model-supplied and goes straight into a URL path, so anything outside this
# set can reshape the request instead of naming a container: ``x/json#`` makes
# a logs read into an inspect (``Config.Env`` included), ``?`` and ``/`` do the
# same by other means. Checked with ``fullmatch`` so a trailing newline cannot
# slip past a ``$``.
CONTAINER_NAME_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*")


def validate_container_name(name: object) -> str:
    """``name`` as a string if it is a legal container name, else ``BadInput``."""
    # A JSON null must not become a container called "None".
    if not isinstance(name, str) or not CONTAINER_NAME_RE.fullmatch(name):
        raise BadInput(
            f"invalid container name {name!r}: use the name or id from docker_ps "
            "(letters, digits, '_', '.', '-')"
        )
    return name


def container_url(base: str, name: object, action: str) -> str:
    """The URL of ``action`` on container ``name``; ``BadInput`` on a bad name."""
    return f"{base.rstrip('/')}/containers/{validate_container_name(name)}/{action}"
