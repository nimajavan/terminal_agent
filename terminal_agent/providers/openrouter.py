"""
OpenRouter Online Provider.
Unified gateway to hundreds of open-source and proprietary models.
"""

import os
from typing import Dict, Optional, Any
from terminal_agent.providers.openai import OpenAIProvider

class OpenRouterProvider(OpenAIProvider):
    name = "openrouter"
    is_offline = False

    def __init__(self, model: str = "meta-llama/llama-3.3-70b-instruct", config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        cfg["endpoint"] = cfg.get("endpoint", "https://openrouter.ai/api/v1")
        cfg["api_key"] = cfg.get("api_key") or os.environ.get("OPENROUTER_API_KEY", "")
        super().__init__(model=model, config=cfg)
