"""Remove credential-shaped text before it reaches errors or reports."""

from __future__ import annotations

import re

_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]+@")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/-]+=*")
_ASSIGNED_SECRET = re.compile(
    r"(?i)\b(access_token|id_token|refresh_token|password|secret)=([^&\s]+)"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)


def redact_secrets(text: str) -> str:
    """Return ``text`` with URL userinfo, bearer tokens, and private keys removed.

    The replacement is stable when it is applied more than once. Ordinary source
    identifiers such as ``SECRET_KEY`` are left in place because they are not an
    assignment of the form ``secret=``.
    """

    redacted = _PRIVATE_KEY.sub("[redacted private key]", text)
    redacted = _USERINFO.sub(r"\1***@", redacted)
    redacted = _BEARER.sub(r"\1***", redacted)
    redacted = _ASSIGNED_SECRET.sub(r"\1=***", redacted)
    return redacted
