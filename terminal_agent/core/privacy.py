"""Best-effort secret filtering for persisted data and model context."""
import os
import re

_KEY = re.compile(r"(?i)(api[_-]?key|access[_-]?token|password|passwd|secret|authorization)")
_ASSIGNMENT = re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|password|passwd|secret|authorization)\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)")
_KNOWN_SECRETS = set()


def register_secrets(config):
    if isinstance(config, dict):
        for key, value in config.items():
            if _KEY.search(str(key)) and isinstance(value, str) and len(value) >= 4:
                _KNOWN_SECRETS.add(value)
            elif isinstance(value, dict):
                register_secrets(value)


def redact(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if _KEY.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    for secret in sorted(_KNOWN_SECRETS, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    for key, secret in os.environ.items():
        if _KEY.search(key) and len(secret) >= 6:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[REDACTED PRIVATE KEY]", value, flags=re.S)
    value = re.sub(r"\b(?:sk-|gsk_|ghp_|github_pat_)[A-Za-z0-9_-]{12,}", "[REDACTED]", value)
    value = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
    value = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", value)
    return _ASSIGNMENT.sub(r"\1[REDACTED]", value)


def terminal_text(value):
    """Untrusted process/model text must not inject terminal control sequences."""
    value = redact(str(value))
    value = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", value)
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    return "".join(c for c in value if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) <= 159))
