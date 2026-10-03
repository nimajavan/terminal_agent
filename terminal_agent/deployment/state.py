"""Private durable job state, append-only events and bounded child processes."""
import json
import os
import re
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from terminal_agent.core.storage import atomic_json, exclusive_lock
from terminal_agent.core.privacy import redact, terminal_text
from .spec import name


def job_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Invalid job/release identifier")
    return value


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def digest_file(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class State:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            self.root.chmod(0o700)
        for folder in ("jobs", "projects", "secrets", "incoming", "backups"):
            (self.root / folder).mkdir(exist_ok=True, mode=0o700)

    def project(self, app):
        path = self.root / "projects" / name(app)
        path.mkdir(exist_ok=True, mode=0o700)
        return path

    def job(self, identifier):
        value = read(self.root / "jobs" / (job_id(identifier) + ".json"))
        if value is None:
            raise ValueError("Unknown job")
        return value

    def save(self, job):
        job["updated"] = time.time()
        atomic_json(self.root / "jobs" / (job_id(job["id"]) + ".json"), job)

    def enqueue(self, operation, app, **payload):
        job = dict(id=uuid.uuid4().hex, operation=operation, app=name(app), status="queued",
                   created=time.time(), updated=time.time(), phase="queued", payload=payload)
        self.save(job)
        return job

    def jobs(self):
        return sorted((read(p) for p in (self.root / "jobs").glob("*.json")), key=lambda j: j["created"])

    def secret(self, reference):
        path = self.root / "secrets" / name(reference)
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing secret: " + reference)
        if os.name == "posix" and path.stat().st_mode & 0o077:
            raise ValueError("Secret permissions must be 0600: " + reference)
        value = path.read_text(encoding="utf-8")
        if not value or len(value) > 65536 or "\x00" in value:
            raise ValueError("Secret is empty or invalid: " + reference)
        return value

    def set_secret(self, reference, value):
        if not value or len(value) > 65536 or "\x00" in value:
            raise ValueError("Invalid secret value")
        private_write(self.root / "secrets" / name(reference), value)

    def event(self, job, phase, message):
        job["phase"] = phase
        self.save(job)
        event = {"time": time.time(), "job": job["id"], "app": job["app"], "phase": phase,
                 "message": terminal_text(redact(message))}
        with open(self.root / "events.jsonl", "a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def private_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".lta-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Runner:
    """Never print commands/output: Docker config, builds and logs may contain secrets."""
    def __init__(self):
        self.redactions = []
        self.last_error = None

    def run(self, args, cwd=None, timeout=300, data=None, output=None, input_stream=None, discard=False):
        self.last_error = None
        # A temporary file avoids unbounded memory when builds are verbose.
        with tempfile.TemporaryFile() as capture, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(args, cwd=str(cwd) if cwd else None, stdin=input_stream if input_stream else (subprocess.PIPE if data is not None else subprocess.DEVNULL),
                                       stdout=output or (subprocess.DEVNULL if discard else capture), stderr=errors, start_new_session=os.name == "posix")
            try:
                process.communicate(data, timeout=timeout)
            except BaseException:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
                raise
            if process.returncode:
                errors.seek(max(0, errors.tell() - 65536))
                detail = errors.read().decode("utf-8", errors="replace")
                for secret in sorted(self.redactions, key=len, reverse=True):
                    if secret:
                        detail = detail.replace(secret, "[REDACTED]")
                self.last_error = terminal_text(redact(detail))[-16384:]
                raise RuntimeError("Command failed (%s, exit %d); inspect sanitized diagnostics" % (Path(args[0]).name, process.returncode))
            if output or discard:
                return ""
            if capture.tell() > 2 * 1024 * 1024:
                raise RuntimeError("Command output exceeds 2 MiB")
            capture.seek(0)
            return capture.read().decode("utf-8", errors="replace").strip()
