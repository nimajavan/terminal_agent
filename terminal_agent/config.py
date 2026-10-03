"""
Configuration manager for Linux Terminal Agent.
Stores user settings in ~/.config/linux-terminal-agent/config.json.
"""

import json
import os
import copy
import math
from pathlib import Path
from typing import Dict, Any, Optional
from terminal_agent.core.storage import atomic_json
from terminal_agent.core.privacy import register_secrets

DEFAULT_CONFIG: Dict[str, Any] = {
    "provider": "ollama",
    "model": "",
    "auto_execute_safe": False,
    "show_explanation": True,
    "interactive_mode": True,
    "timeout": 60,
    "local_only": False,
    "route_simple": True,
    "max_steps": 12,
    "max_model_calls": 20,
    "max_output_tokens": 2048,
    "budget_usd": None,
    "pricing": {},
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
        "model": "gemini-1.5-flash",
        "max_retries": 2
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
        return copy.deepcopy(DEFAULT_CONFIG)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Merge with defaults
            if not isinstance(data, dict):
                raise ValueError("Configuration must be an object")
            merged = copy.deepcopy(DEFAULT_CONFIG)
            for key, value in data.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key].update(value)
                else:
                    merged[key] = value
            validate_config(merged)
            register_secrets(merged)
            return merged
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("Invalid configuration at %s: %s" % (path, exc)) from exc


def save_config(cfg: Dict[str, Any]) -> None:
    path = get_config_path()
    validate_config(cfg)
    register_secrets(cfg)
    atomic_json(path, cfg)


def set_config_value(key: str, value: Any) -> None:
    if not key or any(not part for part in key.split(".")):
        raise ValueError("Invalid configuration key")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    cfg = load_config()
    if "." in key:
        prefix, nested = key.split(".", 1)
        section = cfg.setdefault(prefix, {})
        if not isinstance(section, dict):
            raise ValueError("Cannot set a nested value on %s" % prefix)
        section[nested] = value
    else:
        if key == "provider" and value != cfg.get("provider"):
            cfg["model"] = ""  # A previous provider's global override must not follow the switch.
        cfg[key] = value
    save_config(cfg)


def validate_config(cfg):
    for key in ("provider", "model", "api_key"):
        if key in cfg and not isinstance(cfg[key], str):
            raise ValueError("%s must be a string" % key)
    for key in ("auto_execute_safe", "show_explanation", "interactive_mode", "local_only", "route_simple"):
        if key in cfg and not isinstance(cfg[key], bool):
            raise ValueError("%s must be true or false" % key)
    for key in ("timeout", "max_steps", "max_model_calls", "max_output_tokens"):
        if key in cfg and (type(cfg[key]) is not int or cfg[key] <= 0):
            raise ValueError("%s must be a positive integer" % key)
    budget = cfg.get("budget_usd")
    if budget is not None and (type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0):
        raise ValueError("budget_usd must be a positive number or null")
    for name in ("ollama", "local", "openai", "anthropic", "gemini", "groq", "openrouter", "pricing"):
        if name in cfg and not isinstance(cfg[name], dict):
            raise ValueError("%s must be an object" % name)
        if name != "pricing":
            section = cfg.get(name, {})
            for key in ("model", "api_key", "endpoint", "host"):
                if key in section and not isinstance(section[key], str):
                    raise ValueError("%s.%s must be a string" % (name, key))
            if "timeout" in section and (type(section["timeout"]) is not int or section["timeout"] <= 0):
                raise ValueError("%s.timeout must be a positive integer" % name)
            if "max_retries" in section and (type(section["max_retries"]) is not int or not 0 <= section["max_retries"] <= 5):
                raise ValueError("%s.max_retries must be an integer between 0 and 5" % name)
