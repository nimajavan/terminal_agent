"""Exercise real CLI entry points using only a disposable project and local files."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CLIIntegration(unittest.TestCase):
    def test_skill_repair_export_and_rollback_roundtrip(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="lta-cli-") as temporary:
            base = Path(temporary)
            project = base / "project"
            project.mkdir()
            target = project / "app.conf"
            target.write_text("healthy=false\n", encoding="utf-8")
            env = dict(os.environ, XDG_CONFIG_HOME=str(base / "config"), XDG_DATA_HOME=str(base / "data"), PYTHONDONTWRITEBYTECODE="1")

            def run(*args, input=None):
                result = subprocess.run([sys.executable, "-B", str(root / "lta"), *args],
                                        env=env, input=input, capture_output=True, text=True,
                                        encoding="utf-8", timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout

            run("skills", "install", str(root / "examples/config-repair.skill.json"))
            output = run("skills", "run", "config-repair", "--project", str(project), "-y", input="y\n")
            self.assertIn("completed", output)
            self.assertEqual(target.read_text(), "healthy=true\n")
            run("sessions", "latest", "--project", str(project))
            exported = base / "plan.json"
            run("export-plan", str(exported), "--project", str(project))
            self.assertTrue(exported.is_file())
            paths = list((base / "data/linux-terminal-agent/projects").glob("*/*.json"))
            session = json.loads(paths[0].read_text(encoding="utf-8"))
            backup = session["results"][-1]["backup_id"]
            run("rollback", backup, "--project", str(project), input="y\n")
            self.assertEqual(target.read_text(), "healthy=false\n")

    def test_invalid_cli_combination_fails_before_model_call(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-B", str(root / "lta"), "--adaptive", "inspect"],
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--adaptive requires --agent", result.stderr)
