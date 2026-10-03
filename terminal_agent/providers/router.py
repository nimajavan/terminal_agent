"""Conservative local routing and session-level model usage accounting."""
import json
import math
import time
from time import sleep as retry_sleep
import random
import sys
from terminal_agent.providers.rule_based import RuleBasedProvider
from terminal_agent.core.privacy import redact, terminal_text
from terminal_agent.providers.errors import TemporaryProviderError

SIMPLE = {"show disk usage", "disk usage", "disk space", "فضای دیسک", "show free memory", "free memory", "مصرف رم", "cpu info", "مشخصات پردازنده", "git status", "وضعیت گیت", "open ports", "show open ports", "list open ports", "show listening ports", "list listening ports"}


class ModelRouter:
    def __init__(self, provider, config, records=None):
        self.provider = provider
        self.config = config
        self.records = records if records is not None else []

    def generate(self, prompt, context, history=None, planning=False, reviewing=False, _attempt=0):
        provider = self.provider
        if not reviewing and self.config.get("route_simple", True) and prompt.strip().lower() in SIMPLE:
            provider = RuleBasedProvider()
        if self.config.get("local_only") and provider.name not in {"rule_based", "ollama", "local"}:
            raise ValueError("Local-only policy forbids cloud model calls")
        if len(self.records) >= self.config.get("max_model_calls", 20):
            raise ValueError("Model call limit reached; start a new session deliberately")
        clean_prompt, clean_history = redact(prompt), redact(history or [])
        # Context includes paths/usernames; remove known secrets there too.
        import copy
        clean_context = copy.copy(context)
        for key, value in vars(clean_context).items():
            if isinstance(value, str):
                setattr(clean_context, key, redact(value))
        price = self.config.get("pricing", {}).get(provider.name + "/" + provider.model)
        if price is not None:
            if not isinstance(price, dict) or any(type(price.get(k)) not in (int, float) or not math.isfinite(price[k]) or price[k] < 0 for k in ("input", "output")):
                raise ValueError("Pricing requires nonnegative input/output USD per million tokens")
        local = provider.name in {"rule_based", "ollama", "local"}
        budget = self.config.get("budget_usd")
        # Byte count plus context overhead is a deliberately conservative reservation,
        # not a claim about a remote provider's final bill.
        input_bound = len((clean_prompt + json.dumps(clean_history) + clean_context.system_prompt_context()).encode("utf-8")) + 16000
        reservation = 0.0 if local else ((input_bound * price["input"] + self.config.get("max_output_tokens", 2048) * price["output"]) / 1000000 if price else None)
        spent = sum(r.get("cost_usd") if r.get("cost_usd") is not None else r.get("reserved_usd") or 0 for r in self.records)
        if budget is not None and not local:
            if price is None or any(r.get("cost_usd") is None and r.get("reserved_usd") is None for r in self.records):
                raise ValueError("Budget requires configured pricing for every cloud model used in this session")
            if spent + reservation > budget:
                raise ValueError("Estimated next-call reservation exceeds remaining budget")
        record = {"provider": provider.name, "model": provider.model, "reserved_usd": reservation,
                  "input_tokens": None, "output_tokens": None, "cost_usd": 0.0 if local else None, "status": "started"}
        self.records.append(record)
        started = time.monotonic()
        provider.last_usage = {}
        temporary_error = None
        try:
            if reviewing:
                result = provider.review_evidence(clean_prompt, clean_context, clean_history, self.config.get("max_steps", 12))
            elif planning:
                result = provider.generate_plan(clean_prompt, clean_context, clean_history, self.config.get("max_steps", 12))
            else:
                result = provider.generate(clean_prompt, clean_context, clean_history)
            record["status"] = "completed"
            return result
        except TemporaryProviderError as exc:
            record["status"] = "failed"
            record["http_status"] = exc.status
            temporary_error = exc
        except Exception:
            record["status"] = "failed"
            raise
        finally:
            record["seconds"] = round(time.monotonic() - started, 3)
            usage = provider.last_usage
            record.update(usage)
            if not local and price and all(type(usage.get(k)) is int for k in ("input_tokens", "output_tokens")):
                record["cost_usd"] = (usage["input_tokens"] * price["input"] + usage["output_tokens"] * price["output"]) / 1000000
        # Each retry is a separate accounted call; recursion rechecks both caps.
        if temporary_error is not None:
            retries = provider.config.get("max_retries", 2)
            if _attempt >= retries or len(self.records) >= self.config.get("max_model_calls", 20):
                raise temporary_error
            delay = min(8, 2 ** _attempt) + random.uniform(0, 0.5)
            if temporary_error.retry_after is not None:
                if not math.isfinite(temporary_error.retry_after) or temporary_error.retry_after > 30:
                    raise temporary_error
                delay = max(delay, temporary_error.retry_after)
            print(terminal_text("%s HTTP %s: retry %s/%s in %.1fs" % (provider.name, temporary_error.status, _attempt + 1, retries, delay)), file=sys.stderr)
            retry_sleep(delay)
            return self.generate(prompt, context, history, planning, reviewing, _attempt + 1)

    def summary(self):
        known = sum(r.get("cost_usd") or 0 for r in self.records)
        tokens = sum((r.get("input_tokens") or 0) + (r.get("output_tokens") or 0) for r in self.records)
        unknown = any(r.get("cost_usd") is None for r in self.records)
        token_unknown = any(r.get("input_tokens") is None and r.get("provider") != "rule_based" for r in self.records)
        return "%s calls | %s tokens%s | %.2fs | $%.6f%s" % (
            len(self.records), tokens, " (partial)" if token_unknown else "",
            sum(r.get("seconds", 0) for r in self.records), known, " + unpriced calls" if unknown else " (configured rates)")
