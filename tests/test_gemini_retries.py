"""Transient failures retry through the existing call and budget accounting."""
import io
import json
import unittest
from urllib.error import HTTPError
from unittest.mock import Mock, patch
from terminal_agent.core.context import get_system_context
from terminal_agent.providers.factory import get_provider
from terminal_agent.providers.router import ModelRouter
from terminal_agent.providers.errors import TemporaryProviderError
from terminal_agent.providers.gemini import retry_after_seconds


def failure(code=503, headers=None):
    return HTTPError("https://example.invalid", code, "Unavailable", headers or {},
                     io.BytesIO(json.dumps({"error": {"message": "High demand"}}).encode()))


def success():
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps({
        "candidates": [{"content": {"parts": [{"text": '{"command":"df -h","explanation":"inspect"}'}]}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    }).encode()
    return response


class GeminiRetries(unittest.TestCase):
    def router(self, config=None, retries=2):
        provider = get_provider("gemini", config={"gemini": {"api_key": "fake-gemini-key", "max_retries": retries}})
        return ModelRouter(provider, dict({"route_simple": False}, **(config or {})))

    def test_transient_error_then_success_is_accounted(self):
        router = self.router()
        with patch("terminal_agent.providers.gemini.urllib.request.urlopen", side_effect=[failure(), success()]) as request, patch("terminal_agent.providers.router.retry_sleep") as sleep:
            response = router.generate("inspect disk", get_system_context())
        self.assertEqual(response.command, "df -h")
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once()
        self.assertEqual([r["status"] for r in router.records], ["failed", "completed"])
        self.assertEqual(router.records[1]["input_tokens"], 10)
        sent = request.call_args[0][0]
        self.assertNotIn("key=", sent.full_url)
        self.assertEqual(sent.get_header("X-goog-api-key"), "fake-gemini-key")

    def test_retries_are_bounded(self):
        router = self.router()
        with patch("terminal_agent.providers.gemini.urllib.request.urlopen", side_effect=[failure(), failure(), failure()]) as request, patch("terminal_agent.providers.router.retry_sleep"):
            with self.assertRaises(TemporaryProviderError):
                router.generate("inspect disk", get_system_context())
        self.assertEqual(request.call_count, 3)
        self.assertEqual(len(router.records), 3)

    def test_permanent_errors_not_retried(self):
        for code in [400, 401, 403, 404]:
            with self.subTest(code=code):
                router = self.router()
                with patch("terminal_agent.providers.gemini.urllib.request.urlopen", side_effect=failure(code)) as request, patch("terminal_agent.providers.router.retry_sleep") as sleep:
                    with self.assertRaises(RuntimeError):
                        router.generate("inspect disk", get_system_context())
                request.assert_called_once()
                sleep.assert_not_called()

    def test_call_cap_stops_retry(self):
        router = self.router({"max_model_calls": 1})
        with patch("terminal_agent.providers.gemini.urllib.request.urlopen", side_effect=failure()) as request, patch("terminal_agent.providers.router.retry_sleep") as sleep:
            with self.assertRaises(TemporaryProviderError):
                router.generate("inspect disk", get_system_context())
        request.assert_called_once()
        sleep.assert_not_called()

    def test_budget_rechecked_for_each_retry(self):
        router = self.router()
        router.config.update({"pricing": {"gemini/" + router.provider.model: {"input": 1, "output": 1}}, "budget_usd": .025})
        with patch("terminal_agent.providers.gemini.urllib.request.urlopen", side_effect=failure()) as request, patch("terminal_agent.providers.router.retry_sleep"):
            with self.assertRaisesRegex(ValueError, "budget"):
                router.generate("inspect disk", get_system_context())
        request.assert_called_once()
        self.assertEqual(len(router.records), 1)

    def test_retry_after_respected(self):
        router = self.router()
        with patch("terminal_agent.providers.gemini.urllib.request.urlopen", side_effect=[failure(headers={"Retry-After": "7"}), success()]), patch("terminal_agent.providers.router.retry_sleep") as sleep:
            router.generate("inspect disk", get_system_context())
        self.assertGreaterEqual(sleep.call_args[0][0], 7)

    def test_long_retry_after_not_shortened(self):
        router = self.router()
        with patch("terminal_agent.providers.gemini.urllib.request.urlopen", side_effect=failure(headers={"Retry-After": "3600"})) as request, patch("terminal_agent.providers.router.retry_sleep") as sleep:
            with self.assertRaises(TemporaryProviderError):
                router.generate("inspect disk", get_system_context())
        request.assert_called_once()
        sleep.assert_not_called()

    def test_zero_retries(self):
        router = self.router(retries=0)
        with patch("terminal_agent.providers.gemini.urllib.request.urlopen", side_effect=failure()) as request, patch("terminal_agent.providers.router.retry_sleep") as sleep:
            with self.assertRaises(TemporaryProviderError):
                router.generate("inspect disk", get_system_context())
        request.assert_called_once()
        sleep.assert_not_called()

    def test_exact_port_request_avoids_cloud(self):
        router = self.router({"route_simple": True})
        with patch("terminal_agent.providers.gemini.urllib.request.urlopen") as request:
            response = router.generate("show open ports", get_system_context())
        self.assertEqual(response.provider_name, "rule_based")
        self.assertIn("ss -tulpn", response.command)
        request.assert_not_called()

    def test_compound_port_request_still_uses_model(self):
        router = self.router({"route_simple": True})
        with patch("terminal_agent.providers.gemini.urllib.request.urlopen", return_value=success()) as request:
            router.generate("show open ports and stop nginx", get_system_context())
        request.assert_called_once()

    def test_retry_after_parsing(self):
        self.assertEqual(retry_after_seconds("2"), 2)
        self.assertIsNone(retry_after_seconds("invalid"))
        self.assertEqual(retry_after_seconds("Wed, 01 Jan 2020 00:00:00 GMT"), 0)
