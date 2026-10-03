"""Controller CLI and the small on-server worker command surface."""
import argparse
import getpass
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from terminal_agent.core.storage import atomic_json, exclusive_lock
from terminal_agent.core.privacy import terminal_text
from .spec import load, validate, detect, authorize, name, OPERATIONS, relative
from .state import State, read, job_id, private_write, digest_file
from .remote import Remote, profiles_path, validate_profile, policy
from .source import snapshot, clone, inventory


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parser():
    p = argparse.ArgumentParser(prog="lta ops", description="Deterministic deployment and server operations (no model calls)")
    subs = p.add_subparsers(dest="command", required=True)
    init = subs.add_parser("init", help="Detect a project and generate a deployment manifest")
    init.add_argument("project", nargs="?", default=".")
    init.add_argument("--name", required=True)
    init.add_argument("--domain", required=True)
    init.add_argument("--output", default="lta.json")
    init.add_argument("--context", default=".", help="Monorepo build context relative to the project root")
    inspect = subs.add_parser("inspect", help="Discover project and monorepo runtime entrypoints without executing code")
    inspect.add_argument("project", nargs="?", default=".")
    server = subs.add_parser("server", help="Register or provision an explicitly scoped server")
    actions = server.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add")
    add.add_argument("name")
    add.add_argument("--host", required=True)
    add.add_argument("--root", default="/srv/lta")
    add.add_argument("--port", type=int, default=22)
    add.add_argument("--identity")
    add.add_argument("--known-hosts")
    add.add_argument("--apps", nargs="+", required=True)
    add.add_argument("--domains", nargs="+", required=True)
    add.add_argument("--operations", nargs="+", choices=sorted(OPERATIONS), default=["deploy", "rollback", "backup"])
    add.add_argument("--max-memory-mb", type=int, default=4096)
    add.add_argument("--max-cpus", type=int, default=4)
    actions.add_parser("list")
    setup = actions.add_parser("setup")
    setup.add_argument("name")
    setup.add_argument("--bootstrap", action="store_true", help="Install Docker from its official apt repository (root SSH, Ubuntu/Debian)")
    setup.add_argument("--service", action="store_true", help="Install persistent systemd worker and loopback dashboard (root SSH)")
    deploy = subs.add_parser("deploy", help="Snapshot and queue a release")
    deploy.add_argument("source", nargs="?", default=".")
    deploy.add_argument("--server", required=True)
    deploy.add_argument("--manifest")
    deploy.add_argument("--ref", default="HEAD")
    deploy.add_argument("--dry-run", action="store_true")
    deploy.add_argument("--autonomous", action="store_true", help="Execute within an explicitly registered server policy")
    deploy.add_argument("--wait", action="store_true")
    deploy.add_argument("--wait-timeout", type=int, default=3600)
    retry = subs.add_parser("retry", help="Queue a new attempt from a failed/interrupted job")
    retry.add_argument("id")
    retry.add_argument("--server", required=True)
    retry.add_argument("--migration-resolved", action="store_true", help="Operator verified the interrupted migration; skip it in the new attempt")
    resolve = subs.add_parser("resolve", help="Acknowledge that an uncertain operation was manually inspected/repaired")
    resolve.add_argument("id")
    resolve.add_argument("--server", required=True)
    resolve.add_argument("--acknowledge", action="store_true", required=True)
    for command in ("status", "jobs", "releases", "diagnose", "logs", "rollback", "backup", "restore", "drill", "secret", "watch", "dashboard", "export-backup", "import-backup"):
        command_parser = subs.add_parser(command)
        command_parser.add_argument("--server", required=True)
        if command in {"releases", "diagnose", "logs", "rollback", "backup", "restore", "drill", "export-backup", "import-backup"}:
            command_parser.add_argument("app")
        if command == "jobs":
            command_parser.add_argument("id", nargs="?")
        if command == "rollback":
            command_parser.add_argument("--release")
        if command in {"restore", "drill", "export-backup"}:
            command_parser.add_argument("backup")
        if command == "restore":
            command_parser.add_argument("--allow-data-restore", action="store_true", required=True)
        if command == "secret":
            command_parser.add_argument("reference")
            command_parser.add_argument("--from-env", help="Read value from this controller environment variable")
        if command in {"export-backup", "import-backup"}:
            command_parser.add_argument("file")
    return p


def main(args=None):
    args = parser().parse_args(args)
    if args.command == "inspect":
        emit({"project": str(Path(args.project).resolve()), "candidates": inventory(args.project)})
        return 0
    if args.command == "init":
        context = relative(args.context)
        spec = detect(Path(args.project) / context, args.name, args.domain)
        if context != ".":
            if spec["runtime"] == "compose":
                spec["compose"] = (Path(context) / spec["compose"]).as_posix()
            else:
                spec.setdefault("build", {})["context"] = context
        spec = validate(spec)
        path = Path(args.output)
        if path.exists():
            raise ValueError("Manifest already exists; edit it explicitly")
        atomic_json(path, spec)
        emit({"manifest": str(path.resolve()), "runtime": spec["runtime"], "review": ["port", "health.path", "start", "test", "database", "secrets"]})
        return 0
    profiles = read(profiles_path(), {})
    if args.command == "server":
        if args.action == "list":
            emit(profiles)
            return 0
        if args.action == "add":
            profile = {key: value for key, value in vars(args).items() if key not in {"command", "action", "name"}}
            for key in ("identity", "known_hosts"):
                if profile.get(key):
                    profile[key] = str(Path(profile[key]).expanduser().resolve())
            validate_profile(profile)
            with exclusive_lock(profiles_path().with_suffix(".lock")):
                profiles = read(profiles_path(), {})
                profiles[name(args.name)] = profile
                atomic_json(profiles_path(), profiles)
            emit({"server": args.name, "registered": True, "next": "lta ops server setup " + args.name})
            return 0
        if name(args.name) not in profiles:
            raise ValueError("Unknown server; use lta ops server add")
        remote = Remote(profiles[args.name])
        if args.bootstrap:
            remote.bootstrap()
        remote.install()
        emit(remote.call("setup", payload={"policy": policy(remote.profile), "service": args.service, "runtime": remote.runtime}))
        return 0
    if args.server not in profiles:
        raise ValueError("Unknown server; use lta ops server add")
    profile = profiles[args.server]
    remote = Remote(profile)
    if args.command == "deploy":
        if not 1 <= args.wait_timeout <= 86400:
            raise ValueError("wait-timeout must be between 1 and 86400 seconds")
        with tempfile.TemporaryDirectory(prefix="lta-deploy-") as temporary:
            temporary = Path(temporary)
            project = Path(args.source).expanduser().resolve()
            source_info = {"kind": "directory"}
            if args.source.startswith("https://"):
                project = temporary / "source"
                commit = clone(args.source, args.ref, project)
                source_info = {"kind": "git", "url": args.source, "ref": args.ref, "commit": commit}
            manifest = Path(args.manifest).resolve() if args.manifest else next(
                (project / p for p in ("lta.json", "lta.yaml", "lta.yml") if (project / p).exists()), project / "lta.json")
            spec = load(manifest)
            authorize(profile, spec, "deploy")
            if args.dry_run:
                emit({"server": args.server, "application": spec["name"], "domain": spec["domain"], "source": source_info,
                      "runtime": spec["runtime"], "phases": ["preflight", "snapshot", "dependencies", "build", "test", "backup/migration", "readiness", "switch", "https", "stabilize", "commit"],
                      "secrets_required": sorted(set(spec["secrets"].values()) | ({spec["database"]["password_secret"]} if spec.get("database") else set())),
                      "mutations": False})
                return 0
            if not args.autonomous:
                raise ValueError("Review with --dry-run, then use --autonomous to apply the registered policy")
            archive = temporary / "source.tgz"
            checksum = snapshot(project, archive)
            upload = uuid.uuid4().hex
            remote.install()
            remote.upload(remote.root + "/incoming/" + upload + ".tgz", archive.read_bytes())
            job = remote.call("enqueue", payload={"operation": "deploy", "app": spec["name"], "manifest": spec,
                                                  "upload": upload, "sha256": checksum, "source": source_info})
            remote.wake()
            emit({"job": job["id"], "status": job["status"], "server": args.server})
            if args.wait:
                deadline = time.monotonic() + args.wait_timeout
                last_phase = None
                while time.monotonic() < deadline:
                    job = remote.call("job", job["id"])
                    if job["phase"] != last_phase:
                        emit({"job": job["id"], "phase": job["phase"], "status": job["status"]})
                        last_phase = job["phase"]
                    if job["status"] not in {"queued", "running"}:
                        return 0 if job["status"] == "completed" else 1
                    time.sleep(3)
                print("Wait timed out; the server job continues. Inspect with lta ops jobs.", file=sys.stderr)
                return 2
            return 0
    if args.command == "resolve":
        emit(remote.call("resolve", job_id(args.id)))
    elif args.command == "retry":
        emit(remote.call("retry", job_id(args.id), payload={"migration_resolved": args.migration_resolved}))
        remote.wake()
    elif args.command == "secret":
        value = os.environ.get(args.from_env) if args.from_env else getpass.getpass("Secret value: ")
        if not value:
            raise ValueError("Secret is empty or environment variable is missing")
        emit(remote.call("secret", name(args.reference), payload={"value": value}))
    elif args.command in {"rollback", "backup", "restore", "drill"}:
        payload = {"operation": args.command, "app": name(args.app)}
        if args.command == "rollback":
            payload["release"] = args.release
        if args.command in {"restore", "drill"}:
            payload["backup"] = job_id(args.backup)
        emit(remote.call("enqueue", payload=payload))
        remote.wake()
    elif args.command == "watch":
        remote.install()
        emit(remote.call("setup", payload={"policy": policy(profile), "service": True, "runtime": remote.runtime}))
    elif args.command == "dashboard":
        # Forward only loopback; no public unauthenticated administrative endpoint.
        print("Dashboard: http://127.0.0.1:8787 (requires server setup --service). Ctrl+C closes tunnel.")
        return subprocess.call(remote.ssh_args() + ["-o", "ExitOnForwardFailure=yes", "-N", "-L", "127.0.0.1:8787:127.0.0.1:8787", profile["host"]])
    elif args.command in {"export-backup", "import-backup"}:
        if args.command == "export-backup":
            target = Path(args.file)
            metadata_path = target.with_name(target.name + ".json")
            if target.exists() or metadata_path.exists():
                raise ValueError("Export destination already exists")
            metadata = remote.call("export-backup", name(args.app), job_id(args.backup), timeout=300)
            remote.transfer(remote.root + "/backups/" + args.app + "/" + args.backup + ".dump", target)
            target.chmod(0o600)
            if digest_file(target) != metadata["sha256"]:
                raise ValueError("Downloaded backup checksum mismatch")
            atomic_json(metadata_path, metadata)
            emit({"exported": str(target.resolve())})
        else:
            source = Path(args.file)
            metadata = read(source.with_name(source.name + ".json"))
            if not metadata or metadata["app"] != name(args.app) or digest_file(source) != metadata["sha256"]:
                raise ValueError("Backup metadata/checksum mismatch")
            upload = uuid.uuid4().hex
            remote.transfer(remote.root + "/incoming/" + upload + ".dump", source, upload=True)
            emit(remote.call("import-backup", name(args.app), payload={"metadata": metadata, "upload": upload}, timeout=300))
    else:
        command_args = []
        if hasattr(args, "app"):
            command_args.append(name(args.app))
        if args.command == "jobs" and args.id:
            command_args.append(job_id(args.id))
        emit(remote.call(args.command, *command_args))
    return 0


def worker_main(args=None):
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("command")
    p.add_argument("arguments", nargs="*")
    p.add_argument("--once", action="store_true")
    p.add_argument("--idle-exit", type=int, default=0)
    args = p.parse_args(args)
    from .engine import Engine
    from .observe import summary, diagnose, dashboard
    engine = Engine(args.root)
    state = engine.state
    try:
        if args.command == "worker":
            engine.worker(args.once, args.idle_exit)
            return 0
        if args.command == "dashboard":
            dashboard(engine)
            return 0
        payload = json.load(sys.stdin) if args.command in {"enqueue", "setup", "secret", "import-backup", "retry"} else None
        if args.command == "setup":
            atomic_json(state.root / "policy.json", payload["policy"])
            engine.command("docker", "info", "--format", "{{.ServerVersion}}", timeout=20)
            engine.command("docker", "compose", "version", timeout=20)
            if payload["service"]:
                install_service(engine, payload["runtime"])
            emit({"ready": True, "service": payload["service"]})
        elif args.command == "secret":
            state.set_secret(name(args.arguments[0]), payload["value"])
            emit({"stored": args.arguments[0]})
        elif args.command == "retry":
            old = state.job(args.arguments[0])
            if old["status"] not in {"failed", "needs_attention"} or old["operation"] != "deploy":
                raise ValueError("Only failed/interrupted deployments may be retried; restores require a fresh explicit command")
            new_payload = old["payload"]
            if old.get("migration_started"):
                if not payload["migration_resolved"]:
                    raise ValueError("Inspect database state, then use --migration-resolved to skip the uncertain migration")
                new_payload["manifest"].pop("migration", None)
            authorize(read(state.root / "policy.json"), validate(new_payload["manifest"]), "deploy")
            new_payload["retry_of"] = old["id"]
            job = state.enqueue("deploy", old["app"], **new_payload)
            if old["status"] == "needs_attention":
                old["status"] = "resolved"
                old["resolved_by"] = job["id"]
                state.save(old)
            emit({key: job[key] for key in ("id", "status", "operation", "app")})
        elif args.command == "resolve":
            job = state.job(args.arguments[0])
            if job["status"] not in {"failed", "needs_attention"}:
                raise ValueError("Only failed or uncertain jobs can be resolved")
            if job.get("migration_started"):
                raise ValueError("Use ops retry --migration-resolved for an uncertain migration")
            if job["operation"] == "deploy":
                spec = validate(job["payload"]["manifest"])
            else:
                current = engine.current(job["app"])
                spec = read(engine.release(job["app"], current["release"]) / "manifest.json")
            authorize(read(state.root / "policy.json"), spec, "backup" if job["operation"] == "drill" else job["operation"])
            job["status"] = "resolved"
            state.event(job, "resolved", "Operator acknowledged inspection/repair of uncertain operation")
            emit({"id": job["id"], "status": job["status"]})
        elif args.command == "enqueue":
            app = name(payload.pop("app"))
            operation = payload.pop("operation")
            server_policy = read(state.root / "policy.json")
            if not server_policy:
                raise ValueError("Run server setup before queueing operations")
            if operation == "deploy":
                spec = validate(payload["manifest"])
                if spec["name"] != app:
                    raise ValueError("Application name mismatch")
                job_id(payload["upload"])
                payload["policy"] = server_policy
            else:
                current = engine.current(app)
                if not current:
                    raise ValueError("Application has no active release")
                spec = read(engine.release(app, current["release"]) / "manifest.json")
            authorize(server_policy, spec, "backup" if operation == "drill" else operation)
            job = state.enqueue(operation, app, **payload)
            emit({key: job[key] for key in ("id", "status", "operation", "app")})
        elif args.command in {"status", "jobs", "job"}:
            if args.arguments:
                job = state.job(args.arguments[0])
                emit({k: v for k, v in job.items() if k != "payload"})
            else:
                emit(summary(engine))
        elif args.command == "releases":
            app = name(args.arguments[0])
            emit({"current": engine.current(app), "releases": [p.name for p in (state.project(app) / "releases").glob("*") if p.is_dir()]})
        elif args.command == "diagnose":
            emit(diagnose(engine, name(args.arguments[0])))
        elif args.command == "logs":
            app = name(args.arguments[0])
            # Audit events carry no raw subprocess output or secret environment values.
            events = state.root / "events.jsonl"
            from collections import deque
            records = deque(maxlen=200)
            if events.exists():
                with events.open(encoding="utf-8") as stream:
                    for line in stream:
                        event = json.loads(line)
                        if event["app"] == app:
                            records.append(event)
            emit(list(records))
        elif args.command in {"export-backup", "import-backup"}:
            transfer_backup(engine, args.command, args.arguments, payload)
        else:
            raise ValueError("Unknown worker command")
        return 0
    except Exception as exc:
        # Output only known safe messages; no raw model, source, secret or Docker output.
        print(json.dumps({"error": type(exc).__name__, "message": terminal_text(str(exc))}), file=sys.stderr)
        return 1


def install_service(engine, runtime):
    if os.name != "posix" or os.geteuid() != 0:
        raise ValueError("Persistent systemd installation requires root")
    runtime = Path(runtime).resolve()
    if runtime.parent != engine.state.root / "runtimes" or not runtime.is_file():
        raise ValueError("Runtime must be an installed versioned zipapp")
    for suffix, command in (("worker", "worker"), ("dashboard", "dashboard")):
        text = ("[Unit]\nDescription=LTA " + suffix + "\nAfter=network-online.target docker.service\nWants=network-online.target\n"
                "[Service]\nType=simple\nExecStart=/usr/bin/python3 " + str(runtime) + " --root " + str(engine.state.root) + " " + command + "\n"
                "Restart=always\nRestartSec=10\nUMask=0077\nKillMode=control-group\nTimeoutStopSec=30\n"
                "[Install]\nWantedBy=multi-user.target\n")
        private_write(Path("/etc/systemd/system/lta-" + suffix + ".service"), text)
    engine.command("systemctl", "daemon-reload", timeout=30)
    engine.command("systemctl", "enable", "--now", "lta-worker", "lta-dashboard", timeout=60)
    # Existing workers finish their current operation before adopting an updated runtime on restart.


def transfer_backup(engine, command, arguments, payload):
    app = name(arguments[0])
    current = engine.current(app)
    if not current:
        raise ValueError("An active application is required")
    spec = read(engine.release(app, current["release"]) / "manifest.json")
    authorize(read(engine.state.root / "policy.json", current["policy"]), spec, "backup" if command == "export-backup" else "restore")
    folder = engine.state.root / "backups" / app
    folder.mkdir(exist_ok=True, mode=0o700)
    if command == "export-backup":
        identifier = job_id(arguments[1])
        metadata = read(folder / (identifier + ".json"))
        if not metadata or digest_file(folder / (identifier + ".dump")) != metadata["sha256"]:
            raise ValueError("Backup checksum mismatch")
        emit(metadata)
    else:
        metadata = payload["metadata"]
        identifier = job_id(metadata["id"])
        if metadata["app"] != app:
            raise ValueError("Backup application mismatch")
        source = engine.state.root / "incoming" / (job_id(payload["upload"]) + ".dump")
        with source.open("rb") as stream:
            header = stream.read(5)
        valid_header = header.startswith(b"\x1f\x8b") if metadata.get("kind") == "volume" else header == b"PGDMP"
        if not valid_header or digest_file(source) != metadata["sha256"]:
            raise ValueError("Invalid backup data/checksum")
        path = folder / (identifier + ".dump")
        if path.exists():
            raise ValueError("Backup already exists")
        source.rename(path)
        if os.name == "posix":
            path.chmod(0o600)
        atomic_json(folder / (identifier + ".json"), metadata)
        emit({"imported": identifier})
