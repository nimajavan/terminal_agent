"""Explicit tools with bounded access, reviewable file changes and rollback."""
import difflib
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from time import sleep as network_wait
import uuid
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlparse
from terminal_agent.core.executor import CommandExecutor, ExecutionResult
from terminal_agent.core.safety import analyze_command, DangerLevel, SafetyAssessment
from terminal_agent.core.storage import atomic_json
from terminal_agent.core.privacy import redact

TOOLS = {
    "shell": {"command": str},
    "read_file": {"path": str},
    "write_file": {"path": str, "content": str},
    "change_directory": {"path": str},
    "service": {"name": str, "action": str},
    "http_check": {"url": str},
    "inspect": {"kind": str},
    "network_traffic": {"seconds": int, "interval": int},
}
INSPECTIONS = {
    "git_status": ["git", "-c", "core.fsmonitor=false", "--no-optional-locks", "--no-pager", "status", "--short"],
    "git_diff": ["git", "-c", "core.fsmonitor=false", "--no-pager", "diff", "--no-ext-diff", "--no-textconv", "--stat"],
    "git_log": ["git", "--no-pager", "log", "-5", "--oneline"],
    "docker_containers": ["docker", "ps", "-a", "--no-trunc"],
    "docker_resources": ["docker", "stats", "--no-stream"],
    "nginx_config": ["nginx", "-t"],
}
SERVICE_READ = {"status", "is-active", "is-enabled", "show", "logs"}
SERVICE_WRITE = {"start", "stop", "restart", "reload", "enable", "disable"}


def validate_action(action):
    if not isinstance(action, dict) or set(action) != {"tool", "args"}:
        raise ValueError("Action must contain exactly tool and args")
    tool, args = action["tool"], action["args"]
    if not isinstance(tool, str) or tool not in TOOLS or not isinstance(args, dict):
        raise ValueError("Unknown tool or invalid arguments")
    schema = TOOLS[tool]
    if set(args) != set(schema) or any(type(args[k]) is not t for k, t in schema.items()):
        raise ValueError("Invalid arguments for %s" % tool)
    if any(isinstance(v, str) and len(v) > 256000 for v in args.values()):
        raise ValueError("Tool argument too large")
    if tool == "write_file" and len(args["content"].encode("utf-8")) > 256000:
        raise ValueError("File content must be at most 256 KB encoded as UTF-8")
    if tool == "service":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}", args["name"]):
            raise ValueError("Invalid service name")
        if args["action"] not in SERVICE_READ | SERVICE_WRITE:
            raise ValueError("Invalid service action")
    if tool == "inspect" and args["kind"] not in INSPECTIONS:
        raise ValueError("Unknown inspection kind")
    if tool == "network_traffic" and (not 1 <= args["seconds"] <= 60 or not 1 <= args["interval"] <= min(10, args["seconds"])):
        raise ValueError("Traffic sampling requires seconds 1..60 and interval 1..10, no greater than seconds")
    if tool == "http_check":
        url = urlparse(args["url"])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("HTTP check requires an http(s) URL without credentials")
    if tool == "shell" and not args["command"].strip():
        raise ValueError("Shell command cannot be empty")
    return action


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_network_counters():
    """Linux interface counters, not packet contents or privileged capture."""
    source = Path("/proc/net/dev")
    if not source.is_file():
        raise ValueError("Live traffic sampling requires Linux /proc/net/dev (run inside Linux/WSL)")
    counters = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        name, fields = line.rsplit(":", 1)
        values = fields.split()
        if len(values) < 16:
            continue
        counters[name.strip()] = (int(values[0]), int(values[8]))
    if not counters:
        raise ValueError("No interface counters are available")
    return counters


class ToolRunner:
    def __init__(self, project, storage, timeout=60, stream=True):
        self.project = Path(project).resolve()
        self.cwd = self.project
        self.storage = Path(storage)
        self.executor = CommandExecutor(timeout)
        self.stream = stream
        self.prepared = {}
        self.last_backup = None

    def path(self, value):
        candidate = self.cwd / value
        # Reject all symlink components, including links that currently stay inside root.
        for part in [candidate] + list(candidate.parents):
            if part.is_symlink():
                raise ValueError("Symlink paths are not permitted for file tools")
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(self.project)
        except ValueError:
            raise ValueError("File tools are restricted to this project's directory")
        if any(p.lower() in {".git", ".ssh", ".aws", ".codex", ".agents", ".netrc"} or p.lower().startswith(".env") or re.search(r"(?i)(credentials|secret|\.pem$|\.key$)", p) for p in relative.parts):
            raise ValueError("Protected or secret path is not available to file tools")
        if resolved.exists() and not (resolved.is_file() or resolved.is_dir()):
            raise ValueError("Only ordinary files and directories are supported")
        return resolved

    def assess(self, action):
        validate_action(action)
        tool, args = action["tool"], action["args"]
        if tool == "shell":
            return analyze_command(args["command"])
        if tool in {"read_file", "write_file", "change_directory"}:
            self.path(args["path"])
        mutation = tool == "write_file" or (tool == "service" and args["action"] in SERVICE_WRITE)
        if tool == "inspect" and args["kind"] == "nginx_config":
            mutation = True  # nginx -t may open configured log files.
        if tool == "http_check":
            mutation = urlparse(args["url"]).hostname not in {"localhost", "127.0.0.1", "::1"}
        return SafetyAssessment(DangerLevel.CAUTION if mutation else DangerLevel.SAFE,
                                ["Explicit review required" if mutation else "Bounded inspection tool"], False, mutation)

    def preview(self, action):
        validate_action(action)
        if action["tool"] != "write_file":
            return json.dumps(action, ensure_ascii=False)
        path = self.path(action["args"]["path"])
        old = self._read(path) if path.exists() else None
        content = action["args"]["content"]
        if redact(content) != content or (old is not None and redact(old.decode("utf-8")) != old.decode("utf-8")):
            raise ValueError("File appears to contain secrets; edit it outside the agent")
        self.prepared[str(path)] = (old, content)
        return "".join(difflib.unified_diff((old or b"").decode("utf-8").splitlines(True), content.splitlines(True),
                                             fromfile=str(path), tofile=str(path) + " (proposed)")) or "No content changes"

    def _read(self, path):
        if not path.is_file() or path.stat().st_size > 256000:
            raise ValueError("Expected an ordinary text file of at most 256 KB")
        return path.read_bytes()

    def _replace(self, path, data, mode=0o600):
        if not path.parent.is_dir():
            raise ValueError("Parent directory must already exist")
        fd, tmp = tempfile.mkstemp(prefix=".lta-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def monitor_traffic(self, seconds, interval):
        if seconds > self.executor.default_timeout:
            raise ValueError("Traffic duration exceeds configured timeout")
        previous = read_network_counters()
        started = last = time.monotonic()
        output = ["Time  Interface  RX KiB/s  TX KiB/s\n"]
        retained = len(output[0])
        if self.stream:
            print(output[0], end="", flush=True)
        while True:
            remaining = seconds - (time.monotonic() - started)
            if remaining <= 0:
                break
            network_wait(min(interval, remaining))
            now = time.monotonic()
            current = read_network_counters()
            elapsed = max(now - last, 0.000001)
            lines = []
            for name in sorted(current):
                before = previous.get(name, current[name])
                rx = max(0, current[name][0] - before[0]) / elapsed / 1024
                tx = max(0, current[name][1] - before[1]) / elapsed / 1024
                lines.append("%.1fs  %s  %.2f  %.2f\n" % (now - started, name, rx, tx))
            frame = "".join(lines)
            if self.stream:
                from terminal_agent.core.privacy import terminal_text
                print(terminal_text(frame), end="", flush=True)
            if retained < self.executor.output_limit:
                output.append(frame[:self.executor.output_limit - retained])
            retained += len(frame)
            previous, last = current, now
        if retained > self.executor.output_limit:
            output.append("[Output truncated]\n")
        return "".join(output)

    def run(self, action):
        validate_action(action)
        self.last_backup = None
        tool, args = action["tool"], action["args"]
        start = time.monotonic()
        try:
            if tool == "shell":
                if analyze_command(args["command"]).is_blocked:
                    raise ValueError("Blocked shell command")
                return self.executor.execute(args["command"], cwd=str(self.cwd), stream=self.stream)
            if tool == "service":
                verb, name = args["action"], args["name"]
                if os.name != "posix":
                    raise ValueError("Service tools require Linux with systemd")
                argv = ["journalctl", "--no-pager", "-n", "80", "-u", name] if verb == "logs" else ["systemctl", "--no-pager", verb, name]
                if verb in SERVICE_WRITE and os.geteuid() != 0:
                    argv = ["sudo", "-n"] + argv
                return self.executor.execute(argv, cwd=str(self.cwd), stream=self.stream)
            if tool == "inspect":
                return self.executor.execute(INSPECTIONS[args["kind"]], cwd=str(self.cwd), stream=self.stream)
            if tool == "network_traffic":
                output = self.monitor_traffic(args["seconds"], args["interval"])
                return ExecutionResult(tool, 0, output, "", time.monotonic() - start)
            if tool == "http_check":
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, *unused):
                        return None
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
                try:
                    with opener.open(args["url"], timeout=self.executor.default_timeout) as response:
                        body = response.read(8192).decode("utf-8", "replace")
                        output = "HTTP %s\n%s" % (response.status, body)
                except urllib.error.HTTPError as exc:
                    result = ExecutionResult(tool, 1, "HTTP %s" % exc.code, str(exc), time.monotonic() - start)
                    exc.close()
                    return result
            elif tool == "change_directory":
                target = self.path(args["path"])
                if not target.is_dir():
                    raise ValueError("Directory does not exist")
                self.cwd = target
                output = str(target)
            elif tool == "read_file":
                output = self._read(self.path(args["path"])).decode("utf-8")
            elif tool == "write_file":
                path = self.path(args["path"])
                old = self._read(path) if path.exists() else None
                if self.prepared.pop(str(path), None) != (old, args["content"]):
                    raise ValueError("File/content changed since preview; review a fresh diff")
                if path.exists() and path.stat().st_nlink != 1:
                    raise ValueError("Hard-linked files are not supported")
                mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
                new = args["content"].encode("utf-8")
                backup_id = uuid.uuid4().hex
                directory = self.storage / "backups" / backup_id
                directory.mkdir(parents=True, mode=0o700)
                if old is not None:
                    self._replace(directory / "original", old)
                atomic_json(directory / "metadata.json", {"path": str(path), "existed": old is not None,
                            "before": digest(old or b""), "after": digest(new), "mode": mode, "restored": False})
                self._replace(path, new, mode)
                self.last_backup = backup_id
                output = "File saved. Backup: " + backup_id
            else:
                raise ValueError("Unknown tool")
            return ExecutionResult(tool, 0, redact(output), "", time.monotonic() - start)
        except (OSError, ValueError, UnicodeError) as exc:
            return ExecutionResult(tool, 1, "", str(exc), time.monotonic() - start)

    def _restore_data(self, backup_id):
        if not re.fullmatch(r"[a-f0-9]{32}", backup_id):
            raise ValueError("Invalid backup ID")
        directory = self.storage / "backups" / backup_id
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        path = self.path(metadata["path"])
        if metadata["restored"]:
            raise ValueError("Backup was already restored")
        if not path.exists() or digest(self._read(path)) != metadata["after"]:
            raise ValueError("File changed after agent edit; refusing to overwrite newer changes")
        old = b""
        if metadata["existed"]:
            old = (directory / "original").read_bytes()
            if digest(old) != metadata["before"]:
                raise ValueError("Backup checksum mismatch")
        return directory, metadata, path, old

    def preview_restore(self, backup_id):
        directory, metadata, path, old = self._restore_data(backup_id)
        diff = "".join(difflib.unified_diff(self._read(path).decode("utf-8").splitlines(True), old.decode("utf-8").splitlines(True), fromfile=str(path), tofile=str(path) + " (restored)"))
        return ("Restore file: " if metadata["existed"] else "Remove newly created file: ") + str(path) + "\n" + diff

    def restore(self, backup_id):
        directory, metadata, path, old = self._restore_data(backup_id)
        if metadata["existed"]:
            self._replace(path, old, metadata["mode"])
        else:
            path.unlink()
        metadata["restored"] = True
        atomic_json(directory / "metadata.json", metadata)
        return str(path)
