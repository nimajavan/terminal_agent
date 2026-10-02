"""Providers package for online and offline LLM backends."""
from terminal_agent.providers.base import BaseProvider, AgentResponse
from terminal_agent.providers.ollama import OllamaProvider
from terminal_agent.providers.local_server import LocalServerProvider
from terminal_agent.providers.rule_based import RuleBasedProvider
from terminal_agent.providers.openai import OpenAIProvider
from terminal_agent.providers.anthropic import AnthropicProvider
from terminal_agent.providers.gemini import GeminiProvider
from terminal_agent.providers.groq import GroqProvider
from terminal_agent.providers.openrouter import OpenRouterProvider
from terminal_agent.providers.factory import get_provider, PROVIDER_REGISTRY

__all__ = [
    "BaseProvider", "AgentResponse",
    "OllamaProvider", "LocalServerProvider", "RuleBasedProvider",
    "OpenAIProvider", "AnthropicProvider", "GeminiProvider",
    "GroqProvider", "OpenRouterProvider",
    "get_provider", "PROVIDER_REGISTRY"
]
