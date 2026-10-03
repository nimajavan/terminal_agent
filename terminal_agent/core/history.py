"""
Command execution and conversation history manager.
Maintains history for conversational context and logs executed commands.
"""

import json
import os
import time
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Any, Optional
from terminal_agent.core.privacy import redact
from terminal_agent.core.storage import atomic_json, exclusive_lock
from terminal_agent.core.session import SessionStore

@dataclass
class HistoryEntry:
    timestamp: float
    query: str
    command: str
    explanation: str
    executed: bool
    exit_code: Optional[int]
    provider: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HistoryEntry":
        return cls(**data)


class HistoryManager:
    def __init__(self, history_dir: Optional[str] = None):
        if history_dir:
            self.history_dir = Path(history_dir)
        else:
            self.history_dir = SessionStore().directory

        self.history_file = self.history_dir / "history.json"
        self._entries: List[HistoryEntry] = []
        self._load()

    def _load(self) -> None:
        self._entries = []
        if not self.history_file.exists():
            return
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
                if isinstance(raw, list):
                    for item in raw:
                        if not isinstance(item, dict):
                            continue
                        try:
                            self._entries.append(HistoryEntry.from_dict(item))
                        except TypeError:
                            continue
        except Exception:
            self._entries = []

    def _save(self) -> None:
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            # keep last 500 entries
            data = [e.to_dict() for e in self._entries[-500:]]
            atomic_json(self.history_file, redact(data))
        except (OSError, ValueError) as exc:
            warnings.warn("Command history could not be saved: " + redact(str(exc)), RuntimeWarning)

    def add(self, query: str, command: str, explanation: str, executed: bool, exit_code: Optional[int], provider: str) -> None:
        entry = HistoryEntry(
            timestamp=time.time(),
            query=redact(query),
            command=redact(command),
            explanation=redact(explanation),
            executed=executed,
            exit_code=exit_code,
            provider=provider
        )
        try:
            with exclusive_lock(self.history_dir / "history.lock"):
                self._load()
                self._entries.append(entry)
                self._save()
        except (OSError, ValueError) as exc:
            warnings.warn("Command history could not be saved: " + redact(str(exc)), RuntimeWarning)

    def get_recent(self, limit: int = 10) -> List[HistoryEntry]:
        return self._entries[-limit:]

    def get_context_for_prompt(self, limit: int = 5) -> List[Dict[str, str]]:
        """Return compact history for LLM prompt context."""
        recent = self._entries[-limit:]
        result = []
        for r in recent:
            status = "succeeded" if r.exit_code == 0 else "failed" if r.exit_code is not None else "skipped"
            result.append({
                "user_intent": r.query,
                "command": r.command,
                "status": status
            })
        return result
