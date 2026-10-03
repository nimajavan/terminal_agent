"""Provider failure classification shared by every online backend."""
import datetime
import json
import math
import re
from email.utils import parsedate_to_datetime
from terminal_agent.core.privacy import redact


class TemporaryProviderError(RuntimeError):
    def __init__(self, message, status, retry_after=None, reason=None, retryable=True, scope="model"):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.reason = reason or ("rate_limit" if status == 429 else "capacity")
        self.retryable = retryable
        self.scope = scope


def retry_after_seconds(value):
    if value is None:
        return None
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=datetime.timezone.utc)
            seconds = (date - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, seconds) if math.isfinite(seconds) else None


def http_failure(provider, error):
    """Read and close the error, honoring header and Google's structured retry hints."""
    try:
        body = error.read(16384).decode("utf-8", "replace")
        try:
            data = json.loads(body)
            detail = data.get("error", data) if isinstance(data, dict) else {}
            if not isinstance(detail, dict):
                detail = {}
        except (ValueError, RecursionError):
            detail = {}
        message = str(detail.get("message", body))
        wait = retry_after_seconds((error.headers or {}).get("Retry-After"))
        details = detail.get("details", [])
        if not isinstance(details, list):
            details = []
        quota = str(detail.get("code", "")).lower() in {"insufficient_quota", "billing_hard_limit_reached", "billing_not_active"}
        quota = quota or str(detail.get("type", "")).lower() == "insufficient_quota"
        for entry in details:
            if not isinstance(entry, dict):
                continue
            delay = entry.get("retryDelay")
            if isinstance(delay, str) and re.fullmatch(r"\d+(?:\.\d+)?s", delay):
                parsed = retry_after_seconds(delay[:-1])
                if parsed is not None:
                    wait = max(wait or 0, parsed)
            violations = entry.get("violations", [])
            if isinstance(violations, list):
                for violation in violations:
                    if isinstance(violation, dict):
                        identity = str(violation.get("quotaId", ""))
                        quota = quota or "perday" in identity.lower() or str(violation.get("quotaValue", "")) == "0"
        label = redact("%s API HTTP %s: %s" % (provider, error.code, message))
        if error.code == 429 and quota:
            return TemporaryProviderError(label + "\nQuota or billing is exhausted; check the project's quota/billing. Immediate retries cannot resolve this.", 429, wait, reason="quota", retryable=False, scope="account")
        if error.code in {408, 409, 425, 429, 500, 502, 503, 504, 529}:
            return TemporaryProviderError(label, error.code, wait)
        return RuntimeError(label)
    finally:
        error.close()


def network_failure(provider, error):
    import ssl
    if isinstance(getattr(error, "reason", error), ssl.SSLCertVerificationError):
        return ConnectionError(redact("%s TLS certificate verification failed: %s" % (provider, error)))
    return TemporaryProviderError(redact("%s network error: %s" % (provider, error)), 0, reason="network", scope="account")


class InvalidModelPlan(ValueError):
    """An invalid model response may receive one accounted schema-correction call."""
    def __init__(self, message, raw_output):
        super().__init__(message)
        self.raw_output = raw_output
