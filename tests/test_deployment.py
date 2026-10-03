"""Deployment contracts, privilege boundaries, durable recovery and real local I/O."""
import copy
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from terminal_agent.deployment.spec import validate, authorize, detect, relative
from terminal_agent.deployment.source import snapshot, extract, clone
from terminal_agent.deployment.compose import release_config, restrict, dockerfile
from terminal_agent.deployment.state import State, Runner, read, digest_file
from terminal_agent.deployment.engine import Engine, escape_compose
from terminal_agent.deployment.remote import validate_profile, runtime_bytes, Remote
from terminal_agent.deployment.observe import observe
from terminal_agent.deployment.volumes import check_archive, snapshot_volume
from terminal_agent.deployment.dns import provision
from terminal_agent.core.storage import atomic_json


def manifest(**kwargs):
    return validate(dict({"version": 1, "name": "demo", "domain": "demo.example.com", "runtime": "static", "port": 80,
                          "health": {"attempts": 1, "stabilize": 1}}, **kwargs))


def policy(**kwargs):
    return dict({"apps": ["demo"], "domains": ["demo.example.com"], "operations": ["deploy", "rollback", "backup", "restore", "restart"],
                 "max_memory_mb": 4096, "max_cpus": 4}, **kwargs)


class ManifestTests(unittest.TestCase):
    def test_defaults_do_not_mutate_caller(self):
        raw = {"version": 1, "name": "demo", "domain": "demo.example.com", "runtime": "static", "port": 80}
        value = validate(raw)
        self.assertNotIn("resources", raw)
        self.assertEqual(value["resources"]["memory_mb"], 512)

    def test_unknown_and_malformed_fields_fail_closed(self):
        for change in ({"typo": 1}, {"port": True}, {"name": "../other"}, {"domain": "foo.com\nadmin off"},
                       {"health": {"path": "http://other"}}, {"start": "npm start"}, {"secrets": {"KEY": "../key"}},
                       {"volumes": {"uploads": "/etc"}}, {"build": {"context": "../host"}}, {"resources": {"cpus": 0}},
                       {"environment": {"KEY": "x"}, "secrets": {"KEY": "secret"}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                manifest(**change)

    def test_policy_application_domain_operation_and_resources(self):
        authorize(policy(), manifest(), "deploy")
        for value in (policy(apps=["other"]), policy(domains=["other.example.com"]), policy(operations=[]), policy(max_memory_mb=128)):
            with self.assertRaises(ValueError):
                authorize(value, manifest(), "deploy")

    def test_repair_and_dns_require_separate_permissions(self):
        with self.assertRaises(ValueError):
            authorize(policy(operations=["deploy"]), manifest(monitor={"restart": True}), "deploy")
        dns = {"provider": "cloudflare", "zone": "a" * 32, "token_secret": "dns-token", "address": "8.8.8.8"}
        with self.assertRaises(ValueError):
            authorize(policy(), manifest(dns=dns), "deploy")

    def test_detect_static_and_node(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "index.html").write_text("hello")
            self.assertEqual(detect(root, "demo", "demo.example.com")["runtime"], "static")
            (root / "package.json").write_text(json.dumps({"scripts": {"build": "vite build", "test": "node --test"}}))
            spec = detect(root, "demo", "demo.example.com")
            self.assertEqual(spec["test"], ["npm", "test"])

    def test_path_rejects_posix_windows_and_parent_escapes(self):
        for value in ("../etc", "/etc", "C:/Users", "..\\etc", "a/../../b"):
            with self.assertRaises(ValueError):
                relative(value)


class SourceTests(unittest.TestCase):
    def test_snapshot_excludes_keys_env_git_and_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "source"
            project.mkdir()
            for filename in ("index.html", ".env", ".env.production", "private.pem"):
                (project / filename).write_text(filename)
            (project / "node_modules").mkdir()
            (project / "node_modules/x").write_text("ignored")
            archive = base / "source.tgz"
            digest = snapshot(project, archive)
            self.assertEqual(digest, digest_file(archive))
            extract(archive, base / "output")
            self.assertEqual([p.name for p in (base / "output").iterdir()], ["index.html"])

    def test_tar_traversal_links_and_duplicate_files_rejected(self):
        for filename, kind, repeat in (("../escape", tarfile.REGTYPE, 1), ("/escape", tarfile.REGTYPE, 1),
                                       ("link", tarfile.SYMTYPE, 1), ("duplicate", tarfile.REGTYPE, 2), (".env", tarfile.REGTYPE, 1)):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "test.tgz"
                with tarfile.open(path, "w:gz") as archive:
                    for _ in range(repeat):
                        info = tarfile.TarInfo(filename)
                        info.type = kind
                        info.size = 1 if kind == tarfile.REGTYPE else 0
                        archive.addfile(info, io.BytesIO(b"x"))
                with self.assertRaises(ValueError):
                    extract(path, Path(temporary) / "out")

    def test_git_rejects_credential_and_option_injection(self):
        for url, ref in (("https://secret@github.com/user/repo", "main"), ("file:///etc", "main"), ("https://github.com/user/repo", "--upload-pack=x")):
            with self.assertRaises(ValueError):
                clone(url, ref, ".")


class ComposeTests(unittest.TestCase):
    def test_compose_security_policy_rejects_host_escape_features(self):
        for key, value in (("privileged", True), ("volumes", ["/:/host"]), ("ports", ["5432:5432"]),
                           ("network_mode", "host"), ("devices", ["/dev/sda"]), ("pid", "host"), ("cap_add", ["SYS_ADMIN"])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                restrict({"services": {"app": {"image": "alpine", key: value}}}, Path.cwd())

    def test_per_project_networks_resources_and_secret_escaping(self):
        spec = manifest(volumes={"uploads": "/data/uploads"})
        config = release_config(spec, Path.cwd(), "a" * 32, {"PASSWORD": "a${SECRET}$z"})
        app = config["services"]["app"]
        self.assertEqual(config["networks"]["edge"]["name"], "lta-demo-edge")
        self.assertEqual(app["cap_drop"], ["ALL"])
        self.assertEqual(app["mem_limit"], "512m")
        self.assertNotIn("ports", app)
        self.assertEqual(escape_compose(config)["services"]["app"]["environment"]["PASSWORD"], "a$${SECRET}$$z")

    def test_node_template_uses_lockfile(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "package-lock.json").write_text("{}")
            dockerfile(manifest(runtime="node", start=["node", "server.js"]), source)
            text = (source / "Dockerfile.lta").read_text()
            self.assertIn("RUN npm ci", text)
            self.assertIn('CMD ["node", "server.js"]', text)

    def test_compose_files_cannot_read_host_env_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine = Engine(root / "state")
            (root / "compose.json").write_text(json.dumps({"services": {"app": {"image": "alpine", "env_file": "/etc/private"}}}))
            with self.assertRaises(ValueError):
                engine.load_source_compose(manifest(runtime="compose", compose="compose.json"), root)


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.fail = None

    def run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.fail and self.fail(args):
            raise RuntimeError("fake command failed")
        if "pg_dump" in args:
            kwargs["output"].write(b"PGDMP" + b"fixture" * 10)
        if "image" in args and "inspect" in args:
            return "sha256:" + "f" * 64
        return ""


class SimulatedEngine(Engine):
    def __init__(self, root):
        super().__init__(root, FakeRunner(), sleep=lambda _: None)
        self.routes = []
        self.internal = True
        self.public = True
        self.backups = 0

    def preflight(self, spec):
        pass

    def prepare(self, job, spec):
        release = self.release(spec["name"], job["id"])
        release.mkdir(parents=True, exist_ok=True)
        atomic_json(release / "manifest.json", spec)
        atomic_json(release / "compose.json", {"services": {"app": {"image": "test:latest"}}})
        return release

    def healthy(self, release, spec):
        return self.internal

    def public_health(self, spec, identifier=None):
        return self.public

    def gateway(self, app, domain, identifier, port):
        self.routes.append((identifier, port))

    def backup(self, app, spec=None):
        self.backups += 1
        return {"backup": "c" * 32}


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.engine = SimulatedEngine(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def queue(self, **kwargs):
        return self.engine.state.enqueue("deploy", "demo", manifest=manifest(**kwargs), policy=policy(), upload="a" * 32, sha256="f" * 64)

    def test_success_pins_images_and_commits_release(self):
        job = self.queue()
        self.engine.execute(job)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(self.engine.current("demo")["release"], job["id"])
        config = read(self.engine.release("demo", job["id"]) / "compose.json")
        self.assertTrue(config["services"]["app"]["image"].startswith("sha256:"))

    def test_failed_readiness_never_switches_route(self):
        job = self.queue()
        self.engine.internal = False
        self.engine.execute(job)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.engine.routes, [])
        self.assertIsNone(self.engine.current("demo"))
        self.assertTrue(any("stop" in args for args, _ in self.engine.run.calls))

    def test_failed_public_health_restores_previous_port_and_pointer(self):
        first = self.queue(port=8080)
        self.engine.execute(first)
        second = self.queue(port=8081)
        self.engine.public = False
        self.engine.execute(second)
        self.assertEqual(second["status"], "failed")
        self.assertEqual(self.engine.current("demo")["release"], first["id"])
        self.assertEqual(self.engine.routes[-1], (first["id"], 8080))

    def test_migration_backup_precedes_command_and_failure_requires_attention(self):
        job = self.queue(database={"name": "demo", "user": "demo", "password_secret": "db-password"}, migration=["python", "migrate.py"])
        self.engine.run.fail = lambda args: "migrate.py" in args
        self.engine.execute(job)
        self.assertEqual(self.engine.backups, 1)
        self.assertTrue(job["migration_started"])
        self.assertEqual(job["status"], "needs_attention")
        self.assertIsNone(self.engine.current("demo"))

    def test_revoked_policy_is_checked_at_execution(self):
        job = self.queue()
        atomic_json(self.engine.state.root / "policy.json", policy(operations=[]))
        self.engine.execute(job)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.engine.run.calls, [])

    def test_crashed_migration_is_not_replayed(self):
        job = self.queue()
        job.update(status="running", migration_started=True)
        self.engine.state.save(job)
        self.engine.worker(once=True)
        self.assertEqual(self.engine.state.job(job["id"])["status"], "needs_attention")
        self.assertEqual(self.engine.run.calls, [])

    def test_committed_pointer_reconciles_crash_as_success(self):
        job = self.queue()
        self.engine.execute(job)
        job.update(status="running", committed=False)
        self.engine.state.save(job)
        self.engine.worker(once=True)
        self.assertEqual(self.engine.state.job(job["id"])["status"], "completed")

    def test_rollback_verifies_target_before_switch(self):
        first = self.queue()
        self.engine.execute(first)
        second = self.queue()
        self.engine.execute(second)
        self.engine.rollback("demo")
        self.assertEqual(self.engine.current("demo")["release"], first["id"])

    def test_uncertain_route_recovery_preserves_candidate_for_operator(self):
        job = self.queue()
        self.engine.public = False
        with patch.object(self.engine, "recover_switch", side_effect=RuntimeError("gateway unavailable")):
            self.engine.execute(job)
        self.assertEqual(job["status"], "needs_attention")
        self.assertTrue(job["recovery_failed"])
        self.assertFalse(any("stop" in args for args, _ in self.engine.run.calls))

    def test_rollback_crash_reconciles_committed_target(self):
        first = self.queue()
        self.engine.execute(first)
        second = self.queue()
        self.engine.execute(second)
        job = self.engine.state.enqueue("rollback", "demo")
        self.engine.execute(job)
        self.assertEqual(job["status"], "completed")
        job.update(status="running", committed=False)
        self.engine.state.save(job)
        self.engine.worker(once=True)
        self.assertEqual(self.engine.state.job(job["id"])["status"], "completed")

    def test_unresolved_migration_blocks_unrelated_new_deploy(self):
        old = self.queue()
        old.update(status="needs_attention", migration_started=True)
        self.engine.state.save(old)
        new = self.queue()
        self.engine.execute(new)
        self.assertEqual(new["status"], "failed")
        self.assertEqual(self.engine.run.calls, [])

    def test_failure_events_do_not_leak_exception_secrets(self):
        job = self.queue()
        with patch.object(self.engine, "prepare", side_effect=RuntimeError("password=very-private-value")):
            self.engine.execute(job)
        events = (self.engine.state.root / "events.jsonl").read_text()
        self.assertNotIn("very-private-value", events)
        self.assertNotIn("very-private-value", json.dumps(self.engine.state.job(job["id"])))


class BackupTests(unittest.TestCase):
    def test_backup_checksum_and_restore_drill_use_transaction_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = Engine(temporary, FakeRunner())
            spec = manifest(database={"name": "demo", "user": "demo", "password_secret": "db-password"})
            identifier = "a" * 32
            release = engine.release("demo", identifier)
            release.mkdir(parents=True)
            atomic_json(release / "manifest.json", spec)
            atomic_json(engine.state.project("demo") / "current.json", {"release": identifier, "domain": spec["domain"], "policy": policy()})
            result = engine.backup("demo")
            engine.restore("demo", result["backup"], drill=True)
            calls = [args for args, _ in engine.run.calls]
            self.assertTrue(any("--single-transaction" in args for args in calls))
            self.assertTrue(any("dropdb" in args for args in calls))
            path = engine.state.root / "backups/demo" / (result["backup"] + ".dump")
            path.write_bytes(b"PGDMPcorrupt")
            with self.assertRaises(ValueError):
                engine.restore("demo", result["backup"], drill=True)


class TransportAndCLITests(unittest.TestCase):
    def test_runner_redacts_registered_values_and_terminal_escapes(self):
        runner = Runner()
        runner.redactions = ["opaque-sensitive-value"]
        with self.assertRaises(RuntimeError):
            runner.run([sys.executable, "-c", "import sys; print('opaque-sensitive-value\\x1b[31m',file=sys.stderr); sys.exit(1)"])
        self.assertNotIn("opaque-sensitive-value", runner.last_error)
        self.assertNotIn("\x1b", runner.last_error)

    def test_runner_timeout_terminates_child(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            Runner().run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.05)

    def test_runner_keeps_stderr_out_of_machine_readable_stdout(self):
        result = Runner().run([sys.executable, "-c", "import sys; print('{\"ok\":true}'); print('warning', file=sys.stderr)"])
        self.assertEqual(json.loads(result), {"ok": True})

    def test_runner_failure_does_not_expose_output(self):
        with self.assertRaises(RuntimeError) as error:
            Runner().run([sys.executable, "-c", "import sys; print('my-private-value'); sys.exit(1)"])
        self.assertNotIn("my-private-value", str(error.exception))

    def test_runtime_zip_is_reproducible_and_has_an_entrypoint(self):
        data = runtime_bytes()
        self.assertEqual(data, runtime_bytes())
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertIn("__main__.py", archive.namelist())
            self.assertIn("terminal_agent/deployment/engine.py", archive.namelist())

    def test_ssh_never_disables_host_verification_or_passes_password(self):
        profile = dict(policy(), host="root@server.example.com", root="/srv/lta", port=22)
        remote = Remote(profile)
        self.assertIn("StrictHostKeyChecking=yes", remote.ssh_args())
        for host in ("-oProxyCommand=evil", "root@host;touch x", "root@host\ncommand"):
            with self.assertRaises(ValueError):
                validate_profile(dict(profile, host=host))

    def test_real_cli_init_registration_and_dry_run_do_not_need_models_or_ssh(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            (temporary / "index.html").write_text("hello")
            env = dict(os.environ, XDG_DATA_HOME=str(temporary / "data"), XDG_CONFIG_HOME=str(temporary / "config"))
            def command(*args):
                result = subprocess.run([sys.executable, str(root / "lta"), *args], capture_output=True, text=True,
                                        encoding="utf-8", env=env, cwd=str(temporary), timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            command("ops", "init", ".", "--name", "demo", "--domain", "demo.example.com")
            command("ops", "server", "add", "production", "--host", "root@not-contacted.example.com", "--apps", "demo", "--domains", "demo.example.com")
            output = command("deploy", ".", "--server", "production", "--dry-run")
            self.assertFalse(output["mutations"])
            self.assertEqual(output["application"], "demo")

    def test_worker_zip_runs_without_installing_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime.pyz"
            runtime.write_bytes(runtime_bytes())
            result = subprocess.run([sys.executable, str(runtime), "--root", str(root / "state"), "status"],
                                    cwd=str(root), capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["projects"], [])


class MonitorTests(unittest.TestCase):
    def test_uncertain_restore_blocks_automatic_writer_restarts(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = SimulatedEngine(temporary)
            job = engine.state.enqueue("deploy", "demo", manifest=manifest(monitor={"restart": True}), policy=policy(), upload="a" * 32, sha256="f" * 64)
            engine.execute(job)
            restore = engine.state.enqueue("restore", "demo", backup="b" * 32)
            restore["status"] = "needs_attention"
            engine.state.save(restore)
            with patch("terminal_agent.deployment.observe.diagnose", return_value={"cause": "application_or_dependency", "release": job["id"]}):
                observe(engine)
            self.assertFalse(any("restart" in args for args, _ in engine.run.calls))
            self.assertIn(restore["id"], read(engine.state.project("demo") / "monitor.json")["needs_attention"])

    def test_failed_repairs_consume_budget_and_respect_cooldown(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = SimulatedEngine(temporary)
            spec = manifest(monitor={"restart": True, "cooldown": 60, "max_repairs": 2, "interval": 10})
            job = engine.state.enqueue("deploy", "demo", manifest=spec, policy=policy(), upload="a" * 32, sha256="f" * 64)
            engine.execute(job)
            engine.internal = False
            failure = {"cause": "application_or_dependency", "release": job["id"]}
            with patch("terminal_agent.deployment.observe.diagnose", return_value=failure):
                for now in (1000, 1011, 1061, 2000):
                    with patch("terminal_agent.deployment.observe.time.time", return_value=now):
                        observe(engine)
            repairs = [args for args, _ in engine.run.calls if "restart" in args]
            self.assertEqual(len(repairs), 2)

    def test_no_repair_without_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = SimulatedEngine(temporary)
            job = engine.state.enqueue("deploy", "demo", manifest=manifest(monitor={"restart": True}), policy=policy(), upload="a" * 32, sha256="f" * 64)
            engine.execute(job)
            atomic_json(engine.state.root / "policy.json", policy(operations=["deploy"]))
            with patch("terminal_agent.deployment.observe.diagnose", return_value={"cause": "application_or_dependency", "release": job["id"]}):
                observe(engine)
            self.assertFalse(any("restart" in args for args, _ in engine.run.calls))


class VolumeTests(unittest.TestCase):
    def test_link_archives_are_rejected_before_destructive_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "volume.tgz"
            with tarfile.open(path, "w:gz") as archive:
                member = tarfile.TarInfo("link")
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
                archive.addfile(member)
            with self.assertRaises(ValueError):
                check_archive(path)

    def test_failed_volume_snapshot_resumes_application(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = SimulatedEngine(temporary)
            spec = manifest(volumes={"uploads": "/data/uploads"})
            job = engine.state.enqueue("deploy", "demo", manifest=spec, policy=policy(), upload="a" * 32, sha256="f" * 64)
            engine.execute(job)
            engine.run.calls.clear()
            engine.run.fail = lambda args: "tar" in args
            with self.assertRaises(RuntimeError):
                snapshot_volume(engine, spec, "uploads")
            calls = [args for args, _ in engine.run.calls]
            self.assertTrue(any("stop" in args for args in calls))
            self.assertIn("up", calls[-1])
            self.assertEqual(list((engine.state.root / "backups/demo").glob("*.partial")), [])


class DNSTests(unittest.TestCase):
    def test_existing_matching_record_is_not_rewritten(self):
        from unittest.mock import MagicMock
        spec = manifest(dns={"provider": "cloudflare", "zone": "a" * 32, "token_secret": "dns-key", "address": "8.8.8.8"})
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({"success": True, "result": [{"name": spec["domain"], "type": "A", "content": "8.8.8.8", "proxied": False}]}).encode()
        opener = MagicMock()
        opener.open.return_value = response
        with patch("terminal_agent.deployment.dns.urllib.request.build_opener", return_value=opener):
            self.assertEqual(provision(spec, "opaque-token"), {"changed": False})
        self.assertEqual(opener.open.call_count, 1)

    def test_conflicting_record_fails_without_mutation(self):
        from unittest.mock import MagicMock
        spec = manifest(dns={"provider": "cloudflare", "zone": "a" * 32, "token_secret": "dns-key", "address": "8.8.8.8"})
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({"success": True, "result": [{"name": spec["domain"], "type": "CNAME", "content": "other.example.com"}]}).encode()
        opener = MagicMock()
        opener.open.return_value = response
        with patch("terminal_agent.deployment.dns.urllib.request.build_opener", return_value=opener), self.assertRaises(ValueError):
            provision(spec, "opaque-token")
        self.assertEqual(opener.open.call_count, 1)


if __name__ == "__main__":
    unittest.main()
