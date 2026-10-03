"""Online failure recovery without real services, credentials or waiting."""
import io
import json
import math
import os
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from terminal_agent.config import validate_config
from terminal_agent.core.context import get_system_context
from terminal_agent.providers.errors import TemporaryProviderError, http_failure, network_failure, retry_after_seconds
from terminal_agent.providers.factory import get_provider
from terminal_agent.providers.router import ModelRouter
from terminal_agent.providers.cooldown import CooldownStore


def error(code, details=None, message="High demand", headers=None, extra=None):
    body = {"message": message, **(extra or {})}
    if details is not None:
        body["details"] = details
    return HTTPError("https://example.invalid", code, "Failure", headers or {}, io.BytesIO(json.dumps({"error": body}).encode()))


def response(name):
    raw = '{"command":"pwd","explanation":"inspect"}'
    bodies = {
        "gemini": {"candidates": [{"content": {"parts": [{"text": raw}]}}]},
        "anthropic": {"content": [{"type": "text", "text": raw}]},
    }
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.read.return_value = json.dumps(bodies.get(name, {"choices": [{"message": {"content": raw}}]})).encode()
    return result


class OnlineResilience(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"XDG_DATA_HOME": temporary.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.clock = 1000.0
        clock = patch("terminal_agent.providers.router.time.monotonic", side_effect=lambda: self.clock)
        clock.start()
        self.addCleanup(clock.stop)
        wall_clock = patch("terminal_agent.providers.cooldown.time.time", side_effect=lambda: self.clock)
        wall_clock.start()
        self.addCleanup(wall_clock.stop)
        sleep = patch("terminal_agent.providers.router.retry_sleep", side_effect=self.wait)
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def wait(self, seconds):
        self.clock += seconds

    def router(self, name="gemini", **settings):
        provider = get_provider(name, model="chosen-model", config={name: {"api_key": "test-credential", "max_retries": 4}})
        return ModelRouter(provider, dict(route_simple=False, **settings))

    def location(self, name):
        return "terminal_agent.providers.%s.urllib.request.urlopen" % ("openai" if name in {"groq", "openrouter"} else name)

    def test_all_online_backends_retry_transient_failures(self):
        for name in ("gemini", "openai", "anthropic", "groq", "openrouter"):
            with self.subTest(provider=name):
                router = self.router(name)
                with patch(self.location(name), side_effect=[error(503), response(name)]) as request:
                    result = router.generate("inspect the working directory", get_system_context())
                self.assertEqual(result.command, "pwd")
                self.assertEqual(request.call_count, 2)
                self.assertEqual([r["model"] for r in router.records], ["chosen-model"] * 2)
                self.assertEqual([r["status"] for r in router.records], ["failed", "completed"])

    def test_interleaved_503_429_503_can_recover_on_same_model(self):
        router = self.router()
        with patch(self.location("gemini"), side_effect=[error(503), error(429, [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "32.5s"}]), error(503), response("gemini")]) as request:
            result = router.generate("inspect the working directory", get_system_context())
        self.assertEqual(result.model_name, "chosen-model")
        self.assertEqual(request.call_count, 4)
        self.assertGreaterEqual(self.sleep.call_args_list[1][0][0], 32.5)
        self.assertEqual({r["provider"] for r in router.records}, {"gemini"})
        self.assertIn("retry wait", router.summary())

    def test_429_without_hint_waits_at_least_ten_seconds(self):
        router = self.router()
        with patch(self.location("gemini"), side_effect=[error(429), response("gemini")]):
            router.generate("inspect", get_system_context())
        self.assertGreaterEqual(self.sleep.call_args[0][0], 10)

    def test_daily_and_zero_quota_are_not_retried(self):
        for violation in ({"quotaId": "GenerateRequestsPerDayPerProject", "quotaValue": "100"}, {"quotaId": "RequestsPerMinute", "quotaValue": "0"}):
            with self.subTest(violation=violation):
                failure = http_failure("gemini", error(429, [{"violations": [violation]}]))
                self.assertEqual(failure.reason, "quota")
                self.assertFalse(failure.retryable)

    def test_quota_error_stops_after_one_call_and_survives_new_router(self):
        router = self.router()
        with patch(self.location("gemini"), side_effect=error(429, [{"violations": [{"quotaId": "RequestsPerDay"}]}])) as request:
            with self.assertRaisesRegex(TemporaryProviderError, "quota/billing"):
                router.generate("inspect", get_system_context())
        request.assert_called_once()
        self.sleep.assert_not_called()
        fresh = self.router()
        with patch(self.location("gemini")) as request:
            with self.assertRaisesRegex(TemporaryProviderError, "No request was sent"):
                fresh.generate("inspect", get_system_context())
        request.assert_not_called()
        self.assertEqual(fresh.records, [])

    def test_paid_quota_exhaustion_not_classified_as_short_rate_limit(self):
        failure = http_failure("openai", error(429, extra={"code": "insufficient_quota"}))
        self.assertFalse(failure.retryable)
        self.assertEqual(failure.scope, "account")

    def test_retry_hint_from_body_is_never_shortened(self):
        router = self.router()
        with patch(self.location("gemini"), side_effect=error(429, [{"retryDelay": "3600s"}])) as request:
            with self.assertRaises(TemporaryProviderError):
                router.generate("inspect", get_system_context())
        request.assert_called_once()
        self.sleep.assert_not_called()
        fresh = self.router()
        with patch(self.location("gemini")) as request:
            with self.assertRaises(TemporaryProviderError) as raised:
                fresh.generate("inspect", get_system_context())
        request.assert_not_called()
        self.assertGreaterEqual(raised.exception.retry_after, 3599)

    def test_header_and_body_retry_hints_use_longer_delay(self):
        failure = http_failure("gemini", error(429, [{"retryDelay": "20s"}], headers={"Retry-After": "35"}))
        self.assertEqual(failure.retry_after, 35)

    def test_529_overload_is_retried_for_anthropic(self):
        router = self.router("anthropic")
        with patch(self.location("anthropic"), side_effect=[error(529), response("anthropic")]) as request:
            router.generate("inspect", get_system_context())
        self.assertEqual(request.call_count, 2)

    def test_authentication_errors_do_not_retry_or_create_cooldown(self):
        router = self.router()
        with patch(self.location("gemini"), side_effect=error(401)) as request:
            with self.assertRaises(RuntimeError):
                router.generate("inspect", get_system_context())
        request.assert_called_once()
        self.sleep.assert_not_called()
        self.assertFalse(list(self.root.rglob("*.json")))

    def test_network_timeout_can_recover(self):
        router = self.router()
        with patch(self.location("gemini"), side_effect=[URLError(TimeoutError("temporary timeout")), response("gemini")]) as request:
            router.generate("inspect", get_system_context())
        self.assertEqual(request.call_count, 2)
        self.assertEqual(router.records[0]["failure_reason"], "network")

    def test_tls_certificate_failure_is_not_retried(self):
        failure = network_failure("gemini", URLError(ssl.SSLCertVerificationError("untrusted certificate")))
        self.assertIsInstance(failure, ConnectionError)
        self.assertNotIsInstance(failure, TemporaryProviderError)

    def test_deadline_bounds_request_timeout_and_stops_wait(self):
        router = self.router(retry_deadline=5)
        original = router.provider.timeout
        def fail(*args, **kwargs):
            self.assertLessEqual(kwargs["timeout"], 5)
            self.clock += 4
            raise error(503)
        with patch(self.location("gemini"), side_effect=fail) as request:
            with self.assertRaises(TemporaryProviderError):
                router.generate("inspect", get_system_context())
        request.assert_called_once()
        self.sleep.assert_not_called()
        self.assertEqual(router.provider.timeout, original)

    def test_cooldown_wait_does_not_count_as_a_model_call(self):
        router = self.router()
        router.cooldowns.remember(router.provider, TemporaryProviderError("capacity", 503), 12)
        with patch(self.location("gemini"), return_value=response("gemini")) as request:
            router.generate("inspect", get_system_context())
        request.assert_called_once()
        self.assertEqual(len(router.records), 1)
        self.assertEqual(self.sleep.call_args[0][0], 12)

    def test_cooldown_does_not_cross_credentials(self):
        router = self.router()
        router.cooldowns.remember(router.provider, TemporaryProviderError("capacity", 503), 3600)
        other = get_provider("gemini", model="chosen-model", config={"gemini": {"api_key": "different-credential"}})
        self.assertIsNone(CooldownStore().active(other))
        for path in self.root.rglob("*.json"):
            self.assertNotIn("test-credential", path.read_text(encoding="utf-8"))

    def test_call_limit_is_checked_before_cooldown_wait(self):
        router = self.router(max_model_calls=1)
        router.records.append({"status": "failed"})
        router.cooldowns.remember(router.provider, TemporaryProviderError("capacity", 503), 12)
        with self.assertRaisesRegex(ValueError, "call limit"):
            router.generate("inspect", get_system_context())
        self.sleep.assert_not_called()

    def test_budget_still_blocks_retry(self):
        router = self.router(pricing={"gemini/chosen-model": {"input": 1, "output": 1}}, budget_usd=.025)
        with patch(self.location("gemini"), side_effect=error(503)) as request:
            with self.assertRaisesRegex(ValueError, "budget"):
                router.generate("inspect", get_system_context())
        request.assert_called_once()

    def test_cooldown_can_be_disabled_deliberately(self):
        router = self.router(provider_cooldown=False)
        router.cooldowns.remember(router.provider, TemporaryProviderError("capacity", 503), 3600)
        with patch(self.location("gemini"), return_value=response("gemini")) as request:
            router.generate("inspect", get_system_context())
        request.assert_called_once()
        self.sleep.assert_not_called()

    def test_expired_cooldown_permits_request(self):
        router = self.router()
        router.cooldowns.remember(router.provider, TemporaryProviderError("capacity", 503), 12)
        self.clock += 13
        self.assertIsNone(router.cooldowns.active(router.provider))

    def test_global_retry_limit_applies_without_provider_override(self):
        provider = get_provider("gemini", config={"gemini": {"api_key": "test-credential"}})
        router = ModelRouter(provider, {"route_simple": False, "max_retries": 0})
        with patch(self.location("gemini"), side_effect=error(503)) as request:
            with self.assertRaises(TemporaryProviderError):
                router.generate("inspect", get_system_context())
        request.assert_called_once()
        self.sleep.assert_not_called()

    def test_new_failure_does_not_shorten_or_weaken_shared_cooldown(self):
        router = self.router()
        network = TemporaryProviderError("network", 0, reason="network", scope="account")
        quota = TemporaryProviderError("quota", 429, reason="quota", retryable=False, scope="account")
        router.cooldowns.remember(router.provider, network, 3600)
        router.cooldowns.remember(router.provider, quota, 300)
        active = router.cooldowns.active(router.provider)
        self.assertFalse(active.retryable)
        self.assertEqual(active.retry_after, 3600)
        router.cooldowns.remember(router.provider, network, 7200)
        active = router.cooldowns.active(router.provider)
        self.assertFalse(active.retryable)
        self.assertEqual(active.retry_after, 7200)

    def test_retry_configuration_rejects_invalid_values(self):
        for settings in ({"max_retries": 6}, {"max_retries": True}, {"retry_deadline": math.inf}, {"retry_max_wait": 61}, {"provider_cooldown": "true"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                validate_config(settings)

    def test_invalid_retry_after_is_ignored(self):
        for value in ("nan", "inf", "invalid", None):
            self.assertIsNone(retry_after_seconds(value))

    def test_real_http_transport_recovers_from_503_then_429(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                status = [503, 429, 200][min(len(requests) - 1, 2)]
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if status == 429:
                    self.send_header("Retry-After", "15")
                self.end_headers()
                body = {"error": {"message": "temporary pressure"}} if status != 200 else {"choices": [{"message": {"content": '{"command":"pwd","explanation":"inspect"}'}}]}
                self.wfile.write(json.dumps(body).encode())
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = get_provider("openai", model="chosen-model", config={"openai": {"api_key": "test-credential", "endpoint": "http://127.0.0.1:%s/v1" % server.server_port}})
            router = ModelRouter(provider, {"route_simple": False})
            result = router.generate("inspect", get_system_context())
            self.assertEqual(result.command, "pwd")
            self.assertEqual([r["model"] for r in requests], ["chosen-model"] * 3)
            self.assertGreaterEqual(self.sleep.call_args_list[1][0][0], 15)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
