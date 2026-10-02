"""
Provider Factory and Auto-Fallback Registry.
Instantiates providers by name and automatically selects the best available
offline or online provider based on system environment.
"""

import os
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
    "anthropic": "claude-3-5-sonnet-20241022",
    "gemini": "gemini-1.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "meta-llama/llama-3.3-70b-instruct",
}

def get_provider(
    name: Optional[str] = None,
    model: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None
) -> BaseProvider:
    """Instantiate a provider by name, or auto-detect best available provider."""
    cfg = config or {}
    provider_name = (name or cfg.get("provider") or "").lower().strip()

    # 1. If explicit provider requested, build it
    if provider_name and provider_name in PROVIDER_REGISTRY:
        provider_cls = PROVIDER_REGISTRY[provider_name]
        chosen_model = model or cfg.get("model") or DEFAULT_MODELS.get(provider_name, "default")
        provider_cfg = cfg.get(provider_name, {})
        # Merge top-level api_key if not in provider section
        if "api_key" in cfg and "api_key" not in provider_cfg:
            provider_cfg["api_key"] = cfg["api_key"]
        return provider_cls(model=chosen_model, config=provider_cfg)

    # 2. Auto-detection logic:
    # A. Check if Ollama is running locally
    ollama_test = OllamaProvider(model=DEFAULT_MODELS["ollama"], config=cfg.get("ollama", {}))
    ok, _ = ollama_test.test_connection()
    if ok:
        return ollama_test

    # B. Check for environment API keys
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIProvider(model=DEFAULT_MODELS["openai"])
    if os.environ.get("GROQ_API_KEY"):
        return GroqProvider(model=DEFAULT_MODELS["groq"])
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicProvider(model=DEFAULT_MODELS["anthropic"])
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return GeminiProvider(model=DEFAULT_MODELS["gemini"])

    # C. Default zero-dependency fallback: rule-based offline provider
    return RuleBasedProvider()
