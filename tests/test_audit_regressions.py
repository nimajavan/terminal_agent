"""Reproduce operational failures found during the project audit."""
import copy
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from test_agent import TempCase, file_plan
from terminal_agent.config import DEFAULT_CONFIG
from terminal_agent.core.context import get_system_context
from terminal_agent.core.executor import CommandExecutor
from terminal_agent.core.history import HistoryManager
from terminal_agent.core.skills import step
from terminal_agent.core.tools import ToolRunner, validate_action
from terminal_agent.providers.factory import get_provider
from terminal_agent.providers.transport import read_json_response


class FileAndSessionRegressions(TempCase):
    def test_multibyte_write_limit_is_enforced_before_preview(self):
        action = {"tool": "write_file", "args": {"path": "app.txt", "content": "سلام" * 40000}}
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            validate_action(action)
        self.assertFalse((self.project / "app.txt").exists())

    def test_multibyte_file_at_limit_can_be_written_and_restored(self):
        runner = ToolRunner(self.project, self.base / "storage", stream=False)
        action = {"tool": "write_file", "args": {"path": "app.txt", "content": "س" * 128000}}
        runner.preview(action)
        self.assertTrue(runner.run(action).succeeded)
        self.assertEqual((self.project / "app.txt").stat().st_size, 256000)
        runner.restore(runner.last_backup)
        self.assertFalse((self.project / "app.txt").exists())

    def test_interrupt_is_journaled_and_cannot_replay_automatically(self):
        (self.project / "config.txt").write_text("old")
        workflow = self.workflow(auto_yes=True)
        session = workflow.from_plan(file_plan())
        with patch.object(workflow.tools, "run", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                workflow.run(session)
        saved = workflow.store.load(session["id"])
        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(saved["results"][0]["status"], "interrupted")
        with patch.object(workflow.tools, "run") as execute:
            workflow.run(saved)
        execute.assert_not_called()
        self.assertEqual(saved["status"], "needs_attention")

    def test_history_from_two_loaded_instances_is_not_lost(self):
        first = HistoryManager(str(self.base / "history"))
        second = HistoryManager(str(self.base / "history"))
        first.add("first", "pwd", "inspect", True, 0, "rule_based")
        second.add("second", "pwd", "inspect", True, 0, "rule_based")
        self.assertEqual([e.query for e in HistoryManager(str(self.base / "history")).get_recent()], ["first", "second"])

    def test_one_bad_history_entry_does_not_discard_valid_entries(self):
        history = HistoryManager(str(self.base / "history"))
        history.add("valid", "pwd", "inspect", True, 0, "rule_based")
        data = json.loads(history.history_file.read_text(encoding="utf-8"))
        history.history_file.write_text(json.dumps([{"unknown": 1}] + data), encoding="utf-8")
        self.assertEqual(HistoryManager(str(self.base / "history")).get_recent()[0].query, "valid")

    def test_corrupt_session_is_rejected_and_skipped_in_latest(self):
        workflow = self.workflow()
        session = workflow.from_plan(file_plan())
        path = workflow.store.directory / ("a" * 32 + ".json")
        for corrupt in [[], {"project": str(self.project), "updated": "yesterday"}, dict(session, id="a" * 32, results=[None]), dict(session, id="a" * 32, plan={}), dict(session, id="a" * 32, status="unknown"), dict(session, id="a" * 32, usage=[{"cost_usd": float("nan")}])]:
            with self.subTest(corrupt=corrupt):
                path.write_text(json.dumps(corrupt), encoding="utf-8")
                with self.assertRaises(ValueError):
                    workflow.store.load("a" * 32)
                self.assertEqual(workflow.store.load()["id"], session["id"])

    def test_relative_project_cli_uses_the_correct_directory(self):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, XDG_CONFIG_HOME=str(self.base / "config"), XDG_DATA_HOME=str(self.base / "data"))
        result = subprocess.run([sys.executable, "-B", str(root / "lta"), "--project", "project", "--agent", "-p", "rule_based", "-d", "show disk usage"], cwd=str(self.base), env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        session = self.workflow().store.load()
        self.assertEqual(session["project"], str(self.project))
        self.assertEqual(session["cwd"], str(self.project))

    def test_repl_resume_preserves_dry_run_and_runtime_configuration(self):
        from terminal_agent.cli import run_interactive_repl
        engine = Mock()
        engine.context = get_system_context()
        engine.provider = get_provider("rule_based")
        engine.config = dict(DEFAULT_CONFIG, local_only=True, max_model_calls=1)
        engine.dry_run, engine.auto_yes = True, True
        with patch("builtins.input", side_effect=["resume", "exit"]), patch("terminal_agent.commands.handle_workflow_command") as handle:
            run_interactive_repl(engine)
        self.assertIn("--dry-run", handle.call_args[0][0])
        self.assertIn("--yes", handle.call_args[0][0])
        self.assertEqual(handle.call_args[1]["config"], engine.config)

    def test_repl_dry_run_resume_does_not_modify_files(self):
        from terminal_agent.cli import run_interactive_repl
        target = self.project / "config.txt"
        target.write_text("old")
        workflow = self.workflow()
        workflow.from_plan(file_plan())
        engine = Mock()
        engine.context = get_system_context()
        engine.provider = get_provider("rule_based")
        engine.config = copy.deepcopy(DEFAULT_CONFIG)
        engine.dry_run, engine.auto_yes = True, True
        previous = Path.cwd()
        try:
            os.chdir(self.project)
            with patch("builtins.input", side_effect=["resume", "exit"]):
                run_interactive_repl(engine)
        finally:
            os.chdir(previous)
        self.assertEqual(target.read_text(), "old")
        self.assertEqual(workflow.store.load()["results"], [])


class ExecutorInterruptRegression(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "Real Linux SIGINT and process-group cleanup")
    def test_real_sigint_stops_child_and_returns_130(self):
        import select
        import signal
        import tempfile
        import time
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "child-finished"
            child = "import time; from pathlib import Path; print('READY', flush=True); time.sleep(1); Path(%r).touch(); time.sleep(30)" % str(marker)
            script = ("import sys\nfrom terminal_agent.core.executor import CommandExecutor\n"
                      "try:\n    CommandExecutor().execute([sys.executable, '-c', %r], stream=True)\n"
                      "except KeyboardInterrupt:\n    sys.exit(130)\n") % child
            process = subprocess.Popen([sys.executable, "-B", "-c", script], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            try:
                self.assertTrue(select.select([process.stdout], [], [], 5)[0], "Child did not start")
                self.assertEqual(process.stdout.readline().strip(), b"READY")
                os.kill(process.pid, signal.SIGINT)
                output, errors = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 130, (output, errors))
                time.sleep(1.1)
                self.assertFalse(marker.exists(), "Child survived cancellation")
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=5)

    def test_executor_propagates_ctrl_c_after_cleaning_up(self):
        executor = CommandExecutor()
        process = Mock()
        process.pid = 12345
        process.wait.side_effect = [KeyboardInterrupt, -9]
        process.poll.return_value = None
        with patch("terminal_agent.core.executor.subprocess.Popen", return_value=process), patch("terminal_agent.core.executor.threading.Thread") as thread, patch.object(executor, "cancel") as cancel, patch("terminal_agent.core.executor.os.killpg", create=True):
            with self.assertRaises(KeyboardInterrupt):
                executor.execute(["test-process"])
        cancel.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
        thread.return_value.join.assert_called()
        self.assertIsNone(executor.active)


class ProviderEnvelopeRegressions(unittest.TestCase):
    def test_gemini_defaults_are_consistent(self):
        from terminal_agent.providers.gemini import GeminiProvider
        self.assertEqual(get_provider("gemini").model, DEFAULT_CONFIG["gemini"]["model"])
        self.assertEqual(GeminiProvider().model, DEFAULT_CONFIG["gemini"]["model"])

    def test_claude_default_and_parameters_support_current_models(self):
        from terminal_agent.providers.anthropic import AnthropicProvider
        provider = get_provider("anthropic", config={"anthropic": {"api_key": "fake"}})
        self.assertEqual(provider.model, DEFAULT_CONFIG["anthropic"]["model"])
        self.assertEqual(AnthropicProvider().model, provider.model)
        with patch("terminal_agent.providers.anthropic.urllib.request.urlopen") as transport:
            transport.return_value.__enter__.return_value.read.return_value = b'{"content":[{"type":"text","text":"{}"}]}'
            provider.generate("inspect", get_system_context())
        self.assertNotIn("temperature", json.loads(transport.call_args[0][0].data))

    def test_null_usage_is_unknown_instead_of_crashing(self):
        provider = get_provider("openai", config={"openai": {"api_key": "fake"}})
        provider.record_usage({"usage": None})
        self.assertEqual(provider.last_usage, {"input_tokens": None, "output_tokens": None})

    def test_envelope_validation_bounds_output_and_rejects_nonobjects(self):
        for payload in [b"null", b"[]", b"not json", b"[" * 2000, b"x" * 1048577]:
            with self.subTest(payload=payload[:20]), self.assertRaises(ValueError):
                read_json_response(io.BytesIO(payload))

    def test_all_model_providers_handle_invalid_message_shapes(self):
        cases = {
            "openai": [{"choices": [None]}, {"choices": [{"message": None}]}, {"choices": [{"message": {"content": None}}]}],
            "local": [{"choices": [None]}, {"choices": [{"message": None}]}],
            "ollama": [{"response": None}],
            "gemini": [{"candidates": [None]}, {"candidates": [{"content": None}]}, {"candidates": [{"content": {"parts": [None]}}]}],
            "anthropic": [{"content": [None]}, {"content": [{"type": "text", "text": None}]}],
        }
        for name, responses in cases.items():
            provider = get_provider(name, config={name: {"api_key": "fake"}})
            location = "terminal_agent.providers.%s.%s" % ({"local": "local_server"}.get(name, name), "local_open" if name in {"local", "ollama"} else "urllib.request.urlopen")
            for payload in responses + [None, []]:
                with self.subTest(provider=name, payload=payload), patch(location) as transport:
                    transport.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
                    with self.assertRaises((ValueError, RuntimeError)):
                        provider.generate_plan("inspect a service", get_system_context())
                    self.assertFalse(provider._planning)

    def test_http_failures_are_closed_and_redacted(self):
        for name in ("openai", "anthropic", "local", "ollama"):
            provider = get_provider(name, config={name: {"api_key": "fake"}})
            body = io.BytesIO(b'{"message":"password=private-value"}')
            error = HTTPError("http://localhost/test", 400, "Bad request", {}, body)
            location = "terminal_agent.providers.%s.%s" % ({"local": "local_server"}.get(name, name), "local_open" if name in {"local", "ollama"} else "urllib.request.urlopen")
            with self.subTest(provider=name), patch(location, side_effect=error):
                with self.assertRaises(RuntimeError) as raised:
                    provider.generate("inspect service", get_system_context())
            self.assertNotIn("private-value", str(raised.exception))
            self.assertIn("400", str(raised.exception))
            self.assertTrue(body.closed)

    def test_failed_connectivity_cli_returns_nonzero(self):
        from terminal_agent.cli import _main
        provider = Mock()
        provider.name, provider.model, provider.is_offline = "openai", "test", False
        provider.test_connection.return_value = (False, "Unavailable")
        with patch("sys.argv", ["lta", "test", "openai"]), patch("terminal_agent.cli.load_config", return_value={}), patch("terminal_agent.cli.get_provider", return_value=provider):
            self.assertEqual(_main(), 1)


if __name__ == "__main__":
    unittest.main()
