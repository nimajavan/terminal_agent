"""Bounded repair runbooks, backup scheduling, diagnostics and a read-only dashboard."""
import html
import json
import shutil
import socket
import ssl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from terminal_agent.core.storage import atomic_json
from .state import read
from .spec import authorize


def summary(engine):
    projects = []
    for folder in sorted((engine.state.root / "projects").iterdir()):
        current = read(folder / "current.json")
        if current:
            projects.append({"name": folder.name, "release": current["release"], "previous": current.get("previous"),
                             "domain": current["domain"], "deployed": current["deployed"], "monitor": read(folder / "monitor.json", {})})
    jobs = [{key: job.get(key) for key in ("id", "app", "operation", "status", "phase", "created", "updated", "error")}
            for job in engine.state.jobs()[-100:]]
    return {"projects": projects, "jobs": jobs, "disk_free_mb": shutil.disk_usage(engine.state.root).free // (1024 * 1024)}


def diagnose(engine, app):
    current = engine.current(app)
    if not current:
        return {"app": app, "cause": "no_active_release", "action": "Inspect failed deployment jobs"}
    release = engine.release(app, current["release"])
    spec = read(release / "manifest.json")
    internal = engine.healthy(release, spec)
    public = engine.public_health(spec)
    disk = shutil.disk_usage(engine.state.root).free // (1024 * 1024)
    cause = "healthy" if internal and public else "application_or_dependency" if not internal else "dns_tls_or_gateway"
    if disk < spec["resources"]["min_disk_mb"]:
        cause = "low_disk_space"
    containers = []
    try:
        ids = engine.compose(release, "ps", "-a", "-q", timeout=20).splitlines()
        if ids:
            data = json.loads(engine.command("docker", "inspect", *ids, timeout=20))
            containers = [{"name": item["Name"], "running": item["State"]["Running"], "oom_killed": item["State"].get("OOMKilled"),
                           "exit_code": item["State"].get("ExitCode"), "restarts": item.get("RestartCount", 0)} for item in data]
            if any(c["oom_killed"] for c in containers):
                cause = "memory_limit"
    except (ValueError, RuntimeError, OSError):
        cause = "docker_unavailable"
    certificate_days = None
    try:
        with socket.create_connection((spec["domain"], 443), timeout=5) as connection:
            with ssl.create_default_context().wrap_socket(connection, server_hostname=spec["domain"]) as secure:
                certificate_days = int((ssl.cert_time_to_seconds(secure.getpeercert()["notAfter"]) - time.time()) / 86400)
    except (OSError, KeyError, ValueError):
        pass
    metrics = []
    try:
        if ids:
            text = engine.command("docker", "stats", "--no-stream", "--format", "{{json .}}", *ids, timeout=30)
            for line in text.splitlines():
                item = json.loads(line)
                metrics.append({key: item.get(key) for key in ("Name", "CPUPerc", "MemUsage", "MemPerc", "NetIO", "BlockIO", "PIDs")})
    except (RuntimeError, OSError, ValueError, UnboundLocalError):
        pass
    return {"app": app, "release": current["release"], "cause": cause, "internal_healthy": internal,
            "public_healthy": public, "disk_free_mb": disk, "certificate_days": certificate_days, "containers": containers, "metrics": metrics}


def observe(engine):
    now = time.time()
    for folder in sorted((engine.state.root / "projects").iterdir()):
        current = read(folder / "current.json")
        if not current:
            continue
        spec = read(engine.release(folder.name, current["release"]) / "manifest.json")
        monitor = read(folder / "monitor.json", {})
        if now - monitor.get("checked", 0) < spec["monitor"]["interval"]:
            continue
        result = diagnose(engine, folder.name)
        repairs = monitor.get("repairs", []) if monitor.get("release") == current["release"] else []
        result.update(checked=now, repairs=repairs)
        unresolved = [j["id"] for j in engine.state.jobs() if j["app"] == folder.name and j["status"] == "needs_attention"]
        if unresolved:
            # Never restart writers that a failed restore intentionally left stopped.
            result["needs_attention"] = unresolved
            atomic_json(folder / "monitor.json", result)
            continue
        if result["cause"] == "application_or_dependency" and spec["monitor"]["restart"]:
            policy = read(engine.state.root / "policy.json", current["policy"])
            if len(repairs) < spec["monitor"]["max_repairs"] and (not repairs or now - repairs[-1] >= spec["monitor"]["cooldown"]):
                try:
                    authorize(policy, spec, "restart")
                    # Persist the attempt before executing, so failures also consume the repair budget.
                    repairs.append(now)
                    atomic_json(folder / "monitor.json", result)
                    release = engine.release(folder.name, current["release"])
                    engine.compose(release, "restart", spec["service"], timeout=120)
                    engine.await_health(release, spec)
                    result["repair"] = "restarted_and_verified"
                except (ValueError, RuntimeError, OSError):
                    result["repair"] = "failed_or_denied"
        db = spec.get("database")
        backup_folder = engine.state.root / "backups" / folder.name
        backups = sorted((read(p) for p in backup_folder.glob("*.json")), key=lambda b: b["created"])
        result["last_backup"] = backups[-1]["created"] if backups else None
        has_data = db or spec["volumes"] or spec["redis"]
        backup_hours = db["backup_hours"] if db else spec["monitor"]["backup_hours"]
        if has_data and now - (result["last_backup"] or 0) > backup_hours * 3600:
            # Failed scheduled backups have their own cooldown and don't flood the queue.
            attempts = [j for j in engine.state.jobs() if j["app"] == folder.name and j["operation"] == "backup"]
            if not attempts or (attempts[-1]["status"] not in {"queued", "running"} and now - attempts[-1]["created"] > 3600):
                try:
                    authorize(read(engine.state.root / "policy.json", current["policy"]), spec, "backup")
                    engine.state.enqueue("backup", folder.name)
                except ValueError:
                    result["backup"] = "policy_denied"
        if has_data:
            groups = {}
            for backup in backups:
                groups.setdefault(backup.get("volume", "postgres"), []).append(backup)
            for group in groups.values():
                for backup in group[:-(db["retain"] if db else spec["monitor"]["backup_retain"])]:
                    for suffix in (".dump", ".json"):
                        path = backup_folder / (backup["id"] + suffix)
                        if path.exists():
                            path.unlink()
        atomic_json(folder / "monitor.json", result)
        if result["cause"] != monitor.get("cause") or result.get("repair"):
            event = {"time": now, "app": folder.name, "cause": result["cause"], "repair": result.get("repair")}
            with open(engine.state.root / "alerts.jsonl", "a", encoding="utf-8") as stream:
                stream.write(json.dumps(event) + "\n")


def dashboard(engine, port=8787):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Bound to loopback and read-only, including the JSON endpoint; use SSH port forwarding.
            if self.headers.get("Host", "").split(":")[0] not in {"localhost", "127.0.0.1"}:
                self.send_error(403)
                return
            if self.path not in {"/", "/api/status"}:
                self.send_error(404)
                return
            data = summary(engine)
            if self.path == "/api/status":
                content = json.dumps(data, ensure_ascii=False).encode()
                content_type = "application/json"
            else:
                rows = "".join("<tr>" + "".join("<td>" + html.escape(str(p.get(k, ""))) + "</td>" for k in ("name", "domain", "release")) + "</tr>" for p in data["projects"])
                jobs = "".join("<tr>" + "".join("<td>" + html.escape(str(j.get(k, ""))) + "</td>" for k in ("id", "app", "operation", "status", "phase")) + "</tr>" for j in reversed(data["jobs"]))
                content = ("<!doctype html><html lang=en><meta charset=utf-8><meta http-equiv=refresh content=15>"
                           "<meta name=viewport content='width=device-width,initial-scale=1'><title>LTA Operations</title>"
                           "<style>body{font:16px system-ui;background:#101827;color:#e5edf8;margin:3vw}table{border-collapse:collapse;width:100%;margin:24px 0}"
                           "td,th{padding:12px;text-align:left;border-bottom:1px solid #334155}h1{color:#67e8f9}td{overflow-wrap:anywhere}</style>"
                           "<h1>LTA Operations</h1><p>Read-only · refreshes every 15 seconds · free disk: " + str(data["disk_free_mb"]) +
                           " MiB</p><h2>Applications</h2><table><tr><th>Name</th><th>Domain</th><th>Release</th></tr>" + rows +
                           "</table><h2>Jobs</h2><table><tr><th>Job</th><th>Application</th><th>Operation</th><th>Status</th><th>Phase</th></tr>" + jobs + "</table></html>").encode()
                content_type = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
