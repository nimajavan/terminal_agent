"""Project-isolated, resumable sessions. Interrupted work is never replayed silently."""
import hashlib
import json
import math
import re
import time
import uuid
from pathlib import Path
from terminal_agent.core.storage import atomic_json, data_root
from terminal_agent.core.privacy import redact


class SessionStore:
    def __init__(self, project=None, root=None):
        self.project = Path(project or Path.cwd()).resolve()
        identity = hashlib.sha256(str(self.project).encode()).hexdigest()[:24]
        self.directory = Path(root or data_root()) / "projects" / identity

    def create(self, goal, plan):
        session = {"id": uuid.uuid4().hex, "project": str(self.project), "cwd": str(self.project),
                   "goal": redact(goal), "created": time.time(), "updated": time.time(),
                   "status": "planned", "plan": plan, "results": [], "usage": [], "summary": ""}
        self.save(session)
        return session

    def save(self, session):
        if not re.fullmatch(r"[a-f0-9]{32}", session["id"]):
            raise ValueError("Invalid session ID")
        session["updated"] = time.time()
        atomic_json(self.directory / (session["id"] + ".json"), redact(session))

    def list(self):
        sessions = []
        for path in self.directory.glob("*.json"):
            try:
                sessions.append(self._read(path))
            except (ValueError, OSError):
                continue
        return sorted(sessions, key=lambda s: s.get("updated", 0), reverse=True)

    def load(self, identity="latest"):
        if identity == "latest":
            sessions = self.list()
            if not sessions:
                raise ValueError("No session exists for this project")
            session = sessions[0]
        else:
            if not re.fullmatch(r"[a-f0-9]{32}", identity):
                raise ValueError("Invalid session ID")
            session = self._read(self.directory / (identity + ".json"))
        if session.get("project") != str(self.project):
            raise ValueError("Session belongs to a different project")
        return session

    def _read(self, path):
        try:
            session = json.loads(path.read_text(encoding="utf-8"))
        except RecursionError as exc:
            raise ValueError("Session data is nested too deeply") from exc
        invalid = ValueError("Invalid session data: " + path.name)
        if not isinstance(session, dict):
            raise invalid
        if any(not isinstance(session.get(k), str) for k in ("id", "project", "cwd", "goal", "status", "summary")):
            raise invalid
        if session["id"] != path.stem or not re.fullmatch(r"[a-f0-9]{32}", session["id"]):
            raise invalid
        if session["project"] != str(self.project):
            raise ValueError("Session belongs to a different project")
        if any(type(session.get(k)) not in (int, float) or not math.isfinite(session[k]) for k in ("created", "updated")):
            raise invalid
        if not isinstance(session.get("plan"), dict):
            raise invalid
        if session["status"] not in {"planning", "planning_failed", "planned", "running", "paused", "blocked", "interrupted", "failed", "needs_attention", "completed"}:
            raise invalid
        if session["status"] not in {"planning", "planning_failed"}:
            from terminal_agent.core.planning import validate_plan
            validate_plan(session["plan"], max_steps=max(12, len(session["plan"].get("steps", []))) if isinstance(session["plan"].get("steps"), list) else 12)
        elif not isinstance(session["plan"].get("goal"), str) or not isinstance(session["plan"].get("steps"), list):
            raise invalid
        for key in ("results", "usage"):
            if not isinstance(session.get(key), list) or any(not isinstance(r, dict) for r in session[key]):
                raise invalid
        for record in session["results"]:
            if not isinstance(record.get("step_id"), str) or not isinstance(record.get("status"), str):
                raise invalid
            if "checks" in record and (not isinstance(record["checks"], list) or any(not isinstance(c, dict) for c in record["checks"])):
                raise invalid
        for record in session["usage"]:
            for key in ("cost_usd", "reserved_usd", "seconds", "input_tokens", "output_tokens"):
                value = record.get(key)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise invalid
        return session
