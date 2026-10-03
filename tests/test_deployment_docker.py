"""Opt-in real Docker integration on an isolated Linux CI runner, never a live server."""
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from terminal_agent.deployment.engine import Engine
from terminal_agent.deployment.spec import validate
from terminal_agent.deployment.source import snapshot


@unittest.skipUnless(os.environ.get("LTA_DOCKER_INTEGRATION") == "1", "requires explicit isolated Docker integration environment")
class DockerDeployment(unittest.TestCase):
    def test_real_build_health_switch_and_failed_candidate(self):
        class LocalEngine(Engine):
            # Real Docker build/start/HTTP health; route assertions avoid requiring public DNS/TLS in CI.
            def gateway(self, app, domain, identifier, port):
                self.route = identifier

            def public_health(self, spec, identifier=None):
                return self.healthy(self.release(spec["name"], self.route), spec)

        with tempfile.TemporaryDirectory(prefix="lta-integration-") as temporary:
            base = Path(temporary)
            engine = LocalEngine(base / "state")
            app = "ci-" + uuid.uuid4().hex[:10]
            spec = validate({"version": 1, "name": app, "domain": "ci.example.com", "runtime": "static", "port": 80,
                             "health": {"path": "/", "contains": "deployment-fixture", "attempts": 15, "interval": 1, "stabilize": 1},
                             "resources": {"min_disk_mb": 128}})
            policy = {"apps": [app], "domains": [spec["domain"]], "operations": ["deploy", "rollback"], "max_memory_mb": 1024, "max_cpus": 2}
            source = base / "source"
            source.mkdir()
            (source / "index.html").write_text("deployment-fixture")
            upload = uuid.uuid4().hex
            archive = engine.state.root / "incoming" / (upload + ".tgz")
            checksum = snapshot(source, archive)
            try:
                first = engine.state.enqueue("deploy", app, manifest=spec, policy=policy, upload=upload, sha256=checksum)
                engine.execute(first)
                self.assertEqual(first["status"], "completed", first)
                self.assertTrue(engine.healthy(engine.release(app, first["id"]), spec))
                broken = dict(spec, health=dict(spec["health"], path="/missing", attempts=1))
                second = engine.state.enqueue("deploy", app, manifest=broken, policy=policy, upload=upload, sha256=checksum)
                engine.execute(second)
                self.assertEqual(second["status"], "failed")
                self.assertEqual(engine.current(app)["release"], first["id"])
                self.assertEqual(engine.route, first["id"])
            finally:
                engine.stop_obsolete(app, set())
                for suffix in ("data", "edge"):
                    try:
                        engine.command("docker", "network", "rm", "lta-" + app + "-" + suffix)
                    except RuntimeError:
                        pass
