"""
Anthropic Claude online provider using the Messages API.
Pure standard library implementation.
"""

import os
import json
import urllib.request
import urllib.error
from typing import List, Dict, Tuple, Optional, Any
from terminal_agent.providers.base import BaseProvider, AgentResponse, parse_llm_json_response
from terminal_agent.core.context import SystemContext
from terminal_agent.providers.transport import read_json_response
from terminal_agent.core.privacy import redact

class AnthropicProvider(BaseProvider):
    name = "anthropic"
    is_offline = False

    def __init__(self, model: str = "claude-sonnet-4-6", config: Optional[Dict[str, Any]] = None):
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
            "max_tokens": self.config.get("max_output_tokens", 2048)
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
                res_data = read_json_response(response)
                self.record_usage(res_data)
                content_blocks = res_data.get("content", [])
                if not isinstance(content_blocks, list) or any(not isinstance(b, dict) or (b.get("type") == "text" and not isinstance(b.get("text"), str)) for b in content_blocks):
                    raise ValueError("Anthropic response blocks must contain text")
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
            try:
                err_body = redact(e.read(8192).decode("utf-8", errors="replace"))
                raise RuntimeError(f"Anthropic API HTTP {e.code}: {err_body}") from e
            finally:
                e.close()
        except urllib.error.URLError as e:
            raise ConnectionError(f"Network error connecting to Anthropic: {e.reason}")

    def test_connection(self) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "Anthropic API key is missing."
        return True, f"Anthropic Claude provider configured for model: {self.model}"
