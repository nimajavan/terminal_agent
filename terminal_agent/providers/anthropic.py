"""
Anthropic Claude Online Provider (Claude 3.5 Sonnet, Claude 3 Haiku, etc.).
Pure standard library implementation.
"""

import os
import json
import urllib.request
import urllib.error
from typing import List, Dict, Tuple, Optional, Any
from terminal_agent.providers.base import BaseProvider, AgentResponse, parse_llm_json_response
from terminal_agent.core.context import SystemContext

class AnthropicProvider(BaseProvider):
    name = "anthropic"
    is_offline = False

    def __init__(self, model: str = "claude-3-5-sonnet-20241022", config: Optional[Dict[str, Any]] = None):
        super().__init__(model=model, config=config)
        self.api_key = self.config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        self.endpoint = "https://api.anthropic.com/v1/messages"
        self.timeout = self.config.get("timeout", 45)

    def generate(
        self,
        prompt: str,
        context: SystemContext,
        history: Optional[List[Dict[str, str]]] = None
    ) -> AgentResponse:
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not found. Set it in config or export ANTHROPIC_API_KEY.")

        system_prompt = self._build_system_prompt(context)
        messages = []

        if history:
            for h in history:
                messages.append({"role": "user", "content": h.get("user_intent", "")})
                messages.append({"role": "assistant", "content": json.dumps({"command": h.get("command", ""), "explanation": "Done."})})

        messages.append({"role": "user", "content": f"{prompt}\n\n{self.response_instruction()}"})

        payload = {
            "model": self.model,
            "system": system_prompt,
            "messages": messages,
            "max_tokens": self.config.get("max_output_tokens", 2048),
            "temperature": 0.1
        }

        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                self.record_usage(res_data)
                content_blocks = res_data.get("content", [])
                raw_text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
                cmd, exp = parse_llm_json_response(raw_text)
                return AgentResponse(
                    command=cmd,
                    explanation=exp,
                    provider_name=self.name,
                    model_name=self.model,
                    confidence=0.98,
                    raw_response=raw_text
                )
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Anthropic API HTTP {e.code}: {err_body}")
        except urllib.error.URLError as e:
            raise ConnectionError(f"Network error connecting to Anthropic: {e.reason}")

    def test_connection(self) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "Anthropic API key is missing."
        return True, f"Anthropic Claude provider configured for model: {self.model}"
