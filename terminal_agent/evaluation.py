"""Repeatable, offline scenarios. All mutations stay inside temporary directories."""
import io
import json
import os
import sys
import tempfile
import threading
import time
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from terminal_agent.config import DEFAULT_CONFIG
from terminal_agent.core.executor import CommandExecutor
from terminal_agent.core.safety import analyze_command
from terminal_agent.core.session import SessionStore
from terminal_agent.core.skills import step
from terminal_agent.core.tools import ToolRunner
from terminal_agent.core.workflow import AgentWorkflow
from terminal_agent.providers.rule_based import RuleBasedProvider


def evaluate():
    results = []

    def scenario(name, operation):
        started = time.monotonic()
        try:
            operation()
            results.append({"name": name, "passed": True, "seconds": round(time.monotonic() - started, 3)})
        except Exception as exc:
            results.append({"name": name, "passed": False, "error": str(exc)})

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def safety():
        for command in ["find . -delete", "ls; touch marker", "ss -K", "rm file", "awk 'BEGIN {system(\"touch marker\")}'"]:
            assessment = analyze_command(command)
            require(assessment.requires_confirmation or assessment.is_blocked, command + " was auto-approved")
        for command in ["rm -fr /", "rm --recursive --force /", "rm -r -f /"]:
            require(analyze_command(command).is_blocked, command + " was not blocked")

    with tempfile.TemporaryDirectory(prefix="lta-eval-") as temporary:
        root = Path(temporary)
        project = root / "project"
        project.mkdir()
        store = SessionStore(project, root / "storage")

        def repair():
            target = project / "app.conf"
            target.write_text("healthy=false\n", encoding="utf-8")
            workflow = AgentWorkflow(RuleBasedProvider(), DEFAULT_CONFIG, project, True, store=store)
            plan = {"goal": "Repair invalid application configuration", "summary": "Use a verified edit", "steps": [
                step("inspect", "Inspect config", "read_file", {"path": "app.conf"}),
                step("fix", "Fix config", "write_file", {"path": "app.conf", "content": "healthy=true\n"}, depends_on=["inspect"],
                     verify=[{"tool": "read_file", "args": {"path": "app.conf"}, "contains": "healthy=true"}])
            ]}
            with patch("builtins.input", return_value="y"), redirect_stdout(io.StringIO()):
                session = workflow.run(workflow.from_plan(plan))
            require(session["status"] == "completed", "Repair did not complete")
            require(target.read_text() == "healthy=true\n", "Desired state missing")
            workflow.tools.restore(session["results"][-1]["backup_id"])
            require(target.read_text() == "healthy=false\n", "Rollback failed")
            require(sorted(p.name for p in project.iterdir()) == ["app.conf"], "Unexpected project modifications")

        def containment():
            runner = ToolRunner(project, root / "backups", stream=False)
            for path in ["../outside", ".env", ".ssh/id_rsa"]:
                try:
                    runner.path(path)
                except ValueError:
                    continue
                raise AssertionError("Protected path accepted: " + path)

        def health_and_port():
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200 if self.path == "/health" else 503)
                    self.end_headers()
                    self.wfile.write(b"healthy" if self.path == "/health" else b"unavailable")
                def log_message(self, *args):
                    pass
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            runner = ToolRunner(project, root / "backups", timeout=2, stream=False)
            try:
                url = "http://127.0.0.1:%s" % server.server_port
                require(runner.run({"tool": "http_check", "args": {"url": url + "/health"}}).succeeded, "Healthy service rejected")
                require(not runner.run({"tool": "http_check", "args": {"url": url + "/broken"}}).succeeded, "Broken service accepted")
                import socket
                collision = socket.socket()
                try:
                    try:
                        collision.bind(("127.0.0.1", server.server_port))
                    except OSError:
                        pass
                    else:
                        raise AssertionError("Port collision not reproduced")
                finally:
                    collision.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

        def timeout():
            result = CommandExecutor(0.2).execute([sys.executable, "-c", "import time; time.sleep(5)"])
            require(result.timed_out and result.duration_seconds < 3, "Timeout supervision failed")

        scenario("dangerous commands require review", safety)
        scenario("invalid config: repair, verify, rollback, no unexpected files", repair)
        scenario("protected paths and directory escape", containment)
        scenario("healthy/broken HTTP service and occupied port", health_and_port)
        scenario("hung process timeout", timeout)
    passed = sum(r["passed"] for r in results)
    return {"passed": passed, "failed": len(results) - passed,
            "success_rate": passed / len(results), "scenarios": results,
            "scope": "Local deterministic scenarios; no cloud-model quality or host systemd claim"}
