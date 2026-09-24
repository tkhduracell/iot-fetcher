"""A denylist for the repo snapshot: files the ``code_*`` tools must never
show, even though the snapshot only ever holds git-tracked files.

The snapshot (``ai_brain.repo``) already only contains what codeload's public
tarball exports, which by construction excludes anything ``.gitignore``'d --
a real ``.env`` on rpi5 is never in it. This is defence in depth anyway:
someone can commit a secret-shaped file by mistake, in this repo or a fork,
and the day that happens must not be the day an agent reads it back over an
unauthenticated HTTP-triggered tool call. Applied twice, on purpose:

* at extraction (``RepoSnapshot``/``_safe_extract``), so a matching file is
  never written to ``/memory/_repo`` at all -- the strongest guarantee, since
  a bug anywhere downstream then has nothing to find.
* in every ``code_*`` tool, as a second, independent gate -- belt and braces
  in case a future caller ever reads the snapshot directory some other way.

One predicate, ``is_sensitive``, used by both. Content sniffing only applies
to files small enough to have already passed the size cap the caller is
using (extraction reads before the cap is known; the tools already refuse
anything over ``MAX_FILE_BYTES`` before they would sniff it).
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import PurePosixPath

# Env files. Deliberately excludes the template/example spellings a repo is
# expected to commit and that are, by definition, not secrets -- the whole
# point of a *.example file is to be safe to read.
_ENV_DENY = ("*.env", ".env", ".env.*")
_ENV_ALLOW = ("*.example", "*.template", "*.sample")

# Keys and certificates: fixed extensions/prefixes with no legitimate
# "example" spelling worth allowing back in.
_KEY_GLOBS = ("*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "id_rsa*", "id_ed25519*")

# Named credential stores various tools write to a dotfile with a fixed name.
_NAMED_DENY = (
    ".mcp.json",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    ".htpasswd",
)

# A filename merely *mentioning* one of these words is not automatically a
# secret -- "secrets.py" and "set-github-secrets.sh" are ordinary source
# files whose job is handling secrets, not files that contain one. So this
# only fires on a basename that is not itself a source/doc file: something
# named password.txt, a bare secret.yaml, credentials.json and so on.
_KEYISH_WORDS = ("password", "secret", "credential", "token")
_SOURCE_EXTENSIONS = frozenset({".py", ".ts", ".tsx", ".js", ".go", ".sh", ".md"})

# Directories excluded wholesale, matched as a path segment anywhere in the
# relative path -- not just at the root, since a component could vendor a
# nested .git or its own volumes/ directory.
_DENY_DIR_SEGMENTS = frozenset({"volumes", ".git"})

# JSON content markers for a service-account key or an embedded private key,
# regardless of filename -- these are worth catching even when nothing about
# the name itself looks sensitive.
_JSON_MARKERS = ('"private_key"', '"type": "service_account"', '"type":"service_account"')

MAX_SNIFF_BYTES = 64 * 1024


def _matches_any(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _is_keyish_name(name: str) -> bool:
    lowered = name.lower()
    if not any(word in lowered for word in _KEYISH_WORDS):
        return False
    suffix = PurePosixPath(name).suffix.lower()
    # A source or doc file is exempt even when it happens to contain one of
    # the words -- "redact.py", "set-github-secrets.sh", "TOKEN_DESIGN.md".
    return suffix not in _SOURCE_EXTENSIONS


def _has_denied_dir_segment(relative_path: str) -> bool:
    parts = PurePosixPath(relative_path).parts
    return any(part in _DENY_DIR_SEGMENTS for part in parts)


def _looks_like_service_account(content: bytes) -> bool:
    try:
        text = content.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - decode with errors="replace" never raises
        return False
    if any(marker in text for marker in _JSON_MARKERS):
        return True
    # A stricter check for the exact shape, in case the marker strings above
    # are ever present only as a coincidence of formatting (extra
    # whitespace, single quotes from a hand-edited file): parse and look at
    # the actual keys rather than relying on string matching alone.
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    return data.get("type") == "service_account" or "private_key" in data


def is_sensitive(relative_path: str, content: bytes | None = None) -> bool:
    """Whether ``relative_path`` (repo-relative, forward slashes) must be
    hidden from the ``code_*`` tools and from the extracted snapshot.

    ``content`` is optional: extraction has the bytes in hand already, so it
    passes them for the JSON-shaped checks; a name-only check (``code_list``
    filtering a directory listing) simply skips those.
    """
    path = PurePosixPath(relative_path)
    name = path.name

    if _has_denied_dir_segment(relative_path):
        return True
    if name in _NAMED_DENY:
        return True
    if _matches_any(name, _KEY_GLOBS):
        return True
    if _matches_any(name, _ENV_DENY) and not _matches_any(name, _ENV_ALLOW):
        return True
    if _is_keyish_name(name):
        return True
    if content is not None and name.endswith(".json") and len(content) <= MAX_SNIFF_BYTES:
        if _looks_like_service_account(content):
            return True
    return False
