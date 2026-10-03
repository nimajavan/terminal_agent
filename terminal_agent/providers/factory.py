"""
Explicit provider registry. No implicit network probes or cloud failover.
"""

import copy
import ipaddress
from urllib.parse import urlparse
from typing import Dict, Type, Optional, Any
from terminal_agent.providers.base import BaseProvider
from terminal_agent.providers.ollama import OllamaProvider
from terminal_agent.providers.local_server import LocalServerProvider
from terminal_agent.providers.rule_based import RuleBasedProvider
from terminal_agent.providers.openai import OpenAIProvider
from terminal_agent.providers.anthropic import AnthropicProvider
from terminal_agent.providers.gemini import GeminiProvider
from terminal_agent.providers.groq import GroqProvider
from terminal_agent.providers.openrouter import OpenRouterProvider

PROVIDER_REGISTRY: Dict[str, Type[BaseProvider]] = {
    "ollama": OllamaProvider,
    "local": LocalServerProvider,
    "rule_based": RuleBasedProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
}

DEFAULT_MODELS: Dict[str, str] = {
    "ollama": "qwen2.5-coder:7b",
    "local": "default",
    "rule_based": "builtin-rules",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-6",
    "gemini": "gemini-3.8-flash",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "meta-llama/llama-3.3-70b-instruct",
}

def get_provider(
    name: Optional[str] = None,
    model: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None
) -> BaseProvider:
    """Instantiate the selected provider and enforce model traffic policy."""
    cfg = copy.deepcopy(config or {})
    provider_name = (name or cfg.get("provider") or "rule_based").lower().strip()
    if provider_name not in PROVIDER_REGISTRY:
        raise ValueError("Unknown provider: %s" % provider_name)

    # 1. If explicit provider requested, build it
    if provider_name and provider_name in PROVIDER_REGISTRY:
        provider_cls = PROVIDER_REGISTRY[provider_name]
        provider_cfg = cfg.get(provider_name, {})
        selected_default = cfg.get("model") if provider_name == cfg.get("provider") else None
        chosen_model = model or selected_default or provider_cfg.get("model") or DEFAULT_MODELS.get(provider_name, "default")
        if cfg.get("local_only"):
            if provider_name not in {"ollama", "local", "rule_based"}:
                raise ValueError("Local-only policy forbids cloud providers")
            endpoint = provider_cfg.get("host", "http://localhost:11434") if provider_name == "ollama" else provider_cfg.get("endpoint", "http://localhost:8080/v1")
            parsed = urlparse(endpoint)
            try:
                loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
            except ValueError:
                loopback = False
            if provider_name != "rule_based" and (not loopback or parsed.scheme not in {"http", "https"} or parsed.username):
                raise ValueError("Local-only endpoints must use a loopback address")
        provider_cfg.setdefault("timeout", cfg.get("timeout", 60))
        provider_cfg["max_output_tokens"] = cfg.get("max_output_tokens", 2048)
        # Merge top-level api_key if not in provider section
        if cfg.get("api_key") and not provider_cfg.get("api_key") and provider_name == cfg.get("provider"):
            provider_cfg["api_key"] = cfg["api_key"]
        return provider_cls(model=chosen_model, config=provider_cfg)
