"""Share server cooldowns across CLI processes without storing API keys."""
import hashlib
import json
import math
import time
from pathlib import Path
from terminal_agent.core.storage import atomic_json, data_root, exclusive_lock
from terminal_agent.core.privacy import redact
from terminal_agent.providers.errors import TemporaryProviderError


class CooldownStore:
    def __init__(self, root=None):
        self.directory = Path(root or data_root()) / "provider-cooldowns"

    def _path(self, provider, scope):
        parts = [provider.name]
        for key in ("api_key", "endpoint", "host"):
            value = getattr(provider, key, "")
            parts.append(value if isinstance(value, str) else "")
        parts.extend([scope, provider.model if scope == "model" else ""])
        digest = hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()
        return self.directory / (digest + ".json")

    def active(self, provider):
        active = []
        for scope in ("model", "account"):
            path = self._path(provider, scope)
            if not path.exists():
                continue
            try:
                if path.stat().st_size > 8192:
                    raise ValueError("Cooldown state is too large")
                state = json.loads(path.read_text(encoding="utf-8"))
                until = state["until"]
                if type(until) not in (int, float) or not math.isfinite(until):
                    raise ValueError("Invalid cooldown timestamp")
                if until <= time.time():
                    continue
                if not isinstance(state["message"], str) or type(state["status"]) is not int or type(state["retryable"]) is not bool or state["reason"] not in {"rate_limit", "quota", "capacity", "network"}:
                    raise ValueError("Invalid cooldown state")
                active.append(TemporaryProviderError(state["message"], state["status"], until - time.time(), state["reason"], state["retryable"], scope))
            except (KeyError, TypeError, RecursionError) as exc:
                raise ValueError("Invalid provider cooldown state") from exc
        return max(active, key=lambda e: e.retry_after) if active else None

    def remember(self, provider, error, seconds):
        path = self._path(provider, error.scope)
        with exclusive_lock(path.with_suffix(".lock")):
            until = time.time() + seconds
            if path.exists():
                try:
                    previous = json.loads(path.read_text(encoding="utf-8"))
                    old = previous.get("until", 0)
                    if type(old) in (int, float) and math.isfinite(old):
                        until = max(until, old)
                        if old > time.time() and previous.get("retryable") is False and error.retryable:
                            previous["until"] = until
                            atomic_json(path, previous)
                            return
                except (ValueError, TypeError, AttributeError, OSError, RecursionError):
                    pass
            atomic_json(path, {"until": until, "message": redact(str(error))[:2048],
                              "status": error.status, "reason": error.reason, "retryable": error.retryable})
