"""
Command execution and conversation history manager.
Maintains history for conversational context and logs executed commands.
"""

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Any, Optional

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
            xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
            self.history_dir = Path(xdg_data) / "linux-terminal-agent"

        self.history_file = self.history_dir / "history.json"
        self._entries: List[HistoryEntry] = []
        self._load()

    def _load(self) -> None:
        if not self.history_file.exists():
            return
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
                if isinstance(raw, list):
                    self._entries = [HistoryEntry.from_dict(item) for item in raw if isinstance(item, dict)]
        except Exception:
            self._entries = []

    def _save(self) -> None:
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            # keep last 500 entries
            data = [e.to_dict() for e in self._entries[-500:]]
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def add(self, query: str, command: str, explanation: str, executed: bool, exit_code: Optional[int], provider: str) -> None:
        entry = HistoryEntry(
            timestamp=time.time(),
            query=query,
            command=command,
            explanation=explanation,
            executed=executed,
            exit_code=exit_code,
            provider=provider
        )
        self._entries.append(entry)
        self._save()

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
