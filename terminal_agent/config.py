"""
Configuration manager for Linux Terminal Agent.
Stores user settings in ~/.config/linux-terminal-agent/config.json.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional

DEFAULT_CONFIG: Dict[str, Any] = {
    "provider": "ollama",
    "model": "qwen2.5-coder:7b",
    "auto_execute_safe": False,
    "show_explanation": True,
    "interactive_mode": True,
    "timeout": 60,
    "ollama": {
        "host": "http://localhost:11434",
        "model": "qwen2.5-coder:7b"
    },
    "local": {
        "endpoint": "http://localhost:8080/v1",
        "api_key": "sk-local",
        "model": "default"
    },
    "openai": {
        "api_key": "",
        "model": "gpt-4o-mini"
    },
    "anthropic": {
        "api_key": "",
        "model": "claude-3-5-sonnet-20241022"
    },
    "gemini": {
        "api_key": "",
        "model": "gemini-1.5-flash"
    },
    "groq": {
        "api_key": "",
        "model": "llama-3.3-70b-versatile"
    },
    "openrouter": {
        "api_key": "",
        "model": "meta-llama/llama-3.3-70b-instruct"
    }
}


def get_config_path() -> Path:
    xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return Path(xdg_config) / "linux-terminal-agent" / "config.json"


def load_config() -> Dict[str, Any]:
    path = get_config_path()
    if not path.exists():
        return DEFAULT_CONFIG.copy()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Merge with defaults
            merged = DEFAULT_CONFIG.copy()
            merged.update(data)
            return merged
    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(cfg: Dict[str, Any]) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def set_config_value(key: str, value: Any) -> None:
    cfg = load_config()
    if "." in key:
        parts = key.split(".", 1)
        sub = cfg.setdefault(parts[0], {})
        if isinstance(sub, dict):
            sub[parts[1]] = value
    else:
        cfg[key] = value
    save_config(cfg)
