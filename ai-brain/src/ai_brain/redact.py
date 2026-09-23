"""Mask secret-shaped substrings before external text reaches the model.

Container logs and Home Assistant's error log are free text written by
whatever is running -- an integration, a cloud SDK, a library's debug output.
None of it is trusted, and every so often it contains something that must
never be repeated: a Google API key baked into a request URL, an
``Authorization`` header, a bearer token dumped by a misbehaving client. An
agent that reads a tool result cannot tell a secret from an ordinary log
line, and a persistent memory fact is exactly the place a copy-pasted secret
would live forever and later leak through git history or a Slack export --
so this is the one gate every such string must pass through, applied at
``wrap_external`` (the single fencing choke point every log/web/drive tool
already routes through) and again, defensively, at the memory write path.

Deliberately conservative: an ordinary timestamp, UUID, container id or IP
address is not touched. Only shapes that are themselves recognisable secret
formats, or a value sitting right next to a key-ish word (``key=``,
``token=``, ``Authorization:``, ...), get masked. False negatives (a secret
in a shape this does not recognise) are expected and unavoidable; the goal is
zero false positives on ordinary log text, not perfect coverage.
"""

from __future__ import annotations

import re

MASK = "[REDACTED]"

# Google API keys: "AIza" + 35 URL-safe base64 chars is the fixed shape every
# Google Cloud / Maps / Firebase key uses. Long and specific enough that it
# never collides with ordinary text.
_GOOGLE_API_KEY = re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")

# Google OAuth access/refresh tokens and similar values start "ya29." or
# "1//" (refresh tokens) -- also fixed, recognisable prefixes.
_GOOGLE_OAUTH_TOKEN = re.compile(r"\bya29\.[0-9A-Za-z_-]{20,}\b")
_GOOGLE_REFRESH_TOKEN = re.compile(r"\b1//[0-9A-Za-z_-]{20,}\b")

# Slack tokens: xoxb- (bot), xoxa- (app), xoxp- (user), xoxr- (refresh), and
# the newer xoxe.xox[bp]- rotated variants all start this way.
_SLACK_TOKEN = re.compile(r"\bxox[abpr](?:-[0-9A-Za-z]+)+\b|\bxoxe\.xox[bp]-(?:-?[0-9A-Za-z]+)+\b")

# A JWT: three dot-separated base64url segments, header/payload/signature.
# Anchored on the header always starting "eyJ" (base64 of '{"') so an
# ordinary dotted version string or hostname never matches.
_JWT = re.compile(r"\beyJ[0-9A-Za-z_-]+\.[0-9A-Za-z_-]+\.[0-9A-Za-z_-]{10,}\b")

# "Authorization: Bearer <token>" (and the bare "Bearer <token>" a log line
# sometimes prints on its own). The scheme name is the trigger; whatever
# follows is masked whole regardless of its own shape.
_BEARER = re.compile(r"\b(Bearer)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_AUTH_HEADER = re.compile(r"(Authorization\s*:\s*)([^\r\n]+)", re.IGNORECASE)

# key=value / key: value pairs where the key name says "this is a secret" --
# a query string fragment or a logged kwarg, in either separator style.
# Stops at whitespace, '&', or a quote so it does not eat the rest of a URL
# or a following JSON field.
_KEYISH_WORD = r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|passwd|token|key)"
# The optional \"?/'? before the separator handles JSON's `"key": "value"` --
# the word is immediately followed by its own closing quote, not the colon.
# The value needs at least 8 chars: short enough to catch real secrets
# (nothing credible is shorter), long enough that "retry key=3" or
# "key=on" -- an ordinary flag or setting, not a secret -- is left alone.
_KV_SECRET = re.compile(
    rf"\b({_KEYISH_WORD})[\"']?\s*[=:]\s*[\"']?([^\s&\"'<>]{{8,}})",
    re.IGNORECASE,
)

# A long hex or base64-ish run immediately preceded by a key-ish word and a
# separator other than '=' / ':' (e.g. "token abc123..." in prose, or JSON's
# `"api_key": "..."` already covered above by the quote-stripping in
# _KV_SECRET -- this one is for the cases without = or : at all, such as a
# sentence-style log line). Requires at least 16 chars so short flags/enums
# next to the word "key" (e.g. "cache key hit") are left alone.
_KEYISH_PHRASE = re.compile(
    rf"\b{_KEYISH_WORD}\b[^\S\r\n]+(?:is|was|=|:)?[^\S\r\n]*([A-Za-z0-9+/_-]{{16,}}={{0,2}})",
    re.IGNORECASE,
)

# Emails. Household/account addresses are exactly the kind of detail that
# must not end up quoted in a public repo's memory fact.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def redact(text: str) -> str:
    """Mask secret-shaped substrings in ``text``. Idempotent and cheap."""
    if not text:
        return text

    out = text
    out = _GOOGLE_API_KEY.sub(MASK, out)
    out = _GOOGLE_OAUTH_TOKEN.sub(MASK, out)
    out = _GOOGLE_REFRESH_TOKEN.sub(MASK, out)
    out = _SLACK_TOKEN.sub(MASK, out)
    out = _JWT.sub(MASK, out)
    out = _AUTH_HEADER.sub(lambda m: f"{m.group(1)}{MASK}", out)
    out = _BEARER.sub(lambda m: f"{m.group(1)} {MASK}", out)
    out = _KV_SECRET.sub(lambda m: f"{m.group(1)}={MASK}", out)
    out = _KEYISH_PHRASE.sub(lambda m: m.group(0)[: m.start(1) - m.start(0)] + MASK, out)
    out = _EMAIL.sub(MASK, out)
    return out
