"""
Groq Online Provider (Llama 3.3 70B, DeepSeek R1, etc.).
Ultra-low latency inference for terminal operations.
"""

import os
from typing import Dict, Optional, Any
from terminal_agent.providers.openai import OpenAIProvider

class GroqProvider(OpenAIProvider):
    name = "groq"
    is_offline = False

    def __init__(self, model: str = "llama-3.3-70b-versatile", config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        cfg["endpoint"] = cfg.get("endpoint", "https://api.groq.com/openai/v1")
        cfg["api_key"] = cfg.get("api_key") or os.environ.get("GROQ_API_KEY", "")
        super().__init__(model=model, config=cfg)
