"""Strip credentials from text that may be logged, printed, or stored."""

from __future__ import annotations

import re

# user:password@ in postgres/mysql/http URLs. Password may contain reserved characters.
_URL_USERINFO = re.compile(r"((?:[a-z][a-z0-9+.-]*)://[^/\s:@]+:)([^@\s]+)@", re.I)
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_SK = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")


def redact_secrets(text: str) -> str:
    """Replace passwords and token-shaped secrets. Leave hosts, users, and ports."""
    text = _URL_USERINFO.sub(r"\1***@", text)
    text = _BEARER.sub("Bearer ***", text)
    text = _SK.sub("sk-***", text)
    return text
