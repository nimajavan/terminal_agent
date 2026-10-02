"""Project-isolated, resumable sessions. Interrupted work is never replayed silently."""
import hashlib
import json
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
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict) and value.get("project") == str(self.project):
                    sessions.append(value)
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
            session = json.loads((self.directory / (identity + ".json")).read_text(encoding="utf-8"))
        if session.get("project") != str(self.project):
            raise ValueError("Session belongs to a different project")
        return session
