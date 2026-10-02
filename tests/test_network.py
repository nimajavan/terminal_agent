"""HTTP transport, provider schemas and adaptive feedback integration tests."""
import copy
import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from terminal_agent.core.context import get_system_context
from terminal_agent.core.tools import ToolRunner
from terminal_agent.core.workflow import AgentWorkflow
from terminal_agent.providers.factory import get_provider
from terminal_agent.providers.transport import local_open
from terminal_agent.providers.router import ModelRouter
from terminal_agent.config import DEFAULT_CONFIG
from test_agent import TempCase, file_plan


class HTTPTests(TempCase):
    def setUp(self):
        super().setUp()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/ok")
                else:
                    self.send_response(200 if self.path == "/ok" else 503)
                self.end_headers()
                self.wfile.write(b"ready")
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = "http://127.0.0.1:%s" % self.server.server_port

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def test_http_health_status(self):
        runner = ToolRunner(self.project, self.base / "storage", stream=False)
        self.assertTrue(runner.run({"tool": "http_check", "args": {"url": self.url + "/ok"}}).succeeded)
        self.assertFalse(runner.run({"tool": "http_check", "args": {"url": self.url + "/bad"}}).succeeded)

    def test_http_and_model_redirects_refused(self):
        runner = ToolRunner(self.project, self.base / "storage", stream=False)
        self.assertFalse(runner.run({"tool": "http_check", "args": {"url": self.url + "/redirect"}}).succeeded)
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError) as raised:
            local_open(self.url + "/redirect", timeout=2)
        raised.exception.close()


class ProviderPlanTests(unittest.TestCase):
    def test_each_provider_emits_plan_schema_and_records_usage(self):
        raw = json.dumps(file_plan())
        response_shapes = {
            "openai": {"choices": [{"message": {"content": raw}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}},
            "local": {"choices": [{"message": {"content": raw}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}},
            "ollama": {"response": raw, "prompt_eval_count": 100, "eval_count": 20},
            "anthropic": {"content": [{"type": "text", "text": raw}], "usage": {"input_tokens": 100, "output_tokens": 20}},
            "gemini": {"candidates": [{"content": {"parts": [{"text": raw}]}}], "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20}}
        }
        for name, payload in response_shapes.items():
            with self.subTest(provider=name):
                provider = get_provider(name, config={name: {"api_key": "fake-key"}})
                location = "terminal_agent.providers.%s.%s" % ({"local": "local_server"}.get(name, name), "local_open" if name in {"ollama", "local"} else "urllib.request.urlopen")
                with patch(location) as transport:
                    transport.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
                    plan = provider.generate_plan("repair file", get_system_context())
                    request = transport.call_args[0][0]
                    sent = request.data.decode()
                    self.assertIn("steps", sent)
                    self.assertNotIn("Respond strictly with JSON containing 'command'", sent)
                self.assertEqual(plan["goal"], "Repair test config")
                self.assertEqual(provider.last_usage, {"input_tokens": 100, "output_tokens": 20})
                self.assertFalse(provider._planning)

    def test_usage_cost_is_computed_from_response_tokens(self):
        provider = get_provider("openai", model="test", config={"openai": {"api_key": "fake"}})
        with patch("terminal_agent.providers.openai.urllib.request.urlopen") as transport:
            transport.return_value.__enter__.return_value.read.return_value = json.dumps({"choices": [{"message": {"content": '{"command":"ls","explanation":"inspect"}'}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}).encode()
            router = ModelRouter(provider, {"pricing": {"openai/test": {"input": 2, "output": 5}}})
            router.generate("inspect project structure", get_system_context())
        self.assertAlmostEqual(router.records[0]["cost_usd"], .0003)


class AdaptiveTests(TempCase):
    def test_observations_drive_next_plan(self):
        (self.project / "config.txt").write_text("broken")
        provider = get_provider("openai", config={"openai": {"api_key": "fake"}})
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        workflow = AgentWorkflow(provider, cfg, self.project, auto_yes=True)
        inspection = file_plan()
        inspection["steps"] = inspection["steps"][:1]
        session = workflow.from_plan(inspection)
        with patch.object(provider, "review_evidence", side_effect=[
            {"conclusion": "Observed broken config", "goal_met": False, "next_plan": file_plan()},
            {"conclusion": "Observed verified fixed config", "goal_met": True, "next_plan": None},
        ]) as review, patch("builtins.input", return_value="y"):
            final = workflow.run_adaptive(session, 2)
        self.assertEqual(final["status"], "completed")
        self.assertEqual((self.project / "config.txt").read_text(), "fixed")
        self.assertIn("broken", review.call_args_list[0][0][0])
        self.assertIn("fixed", review.call_args_list[1][0][0])
        self.assertEqual(len(final["usage"]), 2)

    def test_adaptive_round_limit_preserves_remaining_work(self):
        (self.project / "config.txt").write_text("broken")
        provider = get_provider("openai")
        workflow = AgentWorkflow(provider, DEFAULT_CONFIG, self.project, auto_yes=True)
        inspection = file_plan()
        inspection["steps"] = inspection["steps"][:1]
        with patch.object(provider, "review_evidence", return_value={"conclusion": "Needs repair", "goal_met": False, "next_plan": file_plan()}):
            result = workflow.run_adaptive(workflow.from_plan(inspection), 1)
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual((self.project / "config.txt").read_text(), "broken")
        self.assertIsNotNone(result["review"]["next_plan"])
