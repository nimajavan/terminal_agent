"""
OpenAI Online Provider (GPT-4o, GPT-4o-mini, etc.).
Pure standard library implementation (no heavy external dependencies required).
"""

import os
import json
import urllib.request
import urllib.error
from typing import List, Dict, Tuple, Optional, Any
from terminal_agent.providers.base import BaseProvider, AgentResponse, parse_llm_json_response
from terminal_agent.core.context import SystemContext

class OpenAIProvider(BaseProvider):
    name = "openai"
    is_offline = False

    def __init__(self, model: str = "gpt-4o-mini", config: Optional[Dict[str, Any]] = None):
        super().__init__(model=model, config=config)
        self.api_key = self.config.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
        self.endpoint = self.config.get("endpoint", "https://api.openai.com/v1").rstrip("/")
        self.timeout = self.config.get("timeout", 45)

    def generate(
        self,
        prompt: str,
        context: SystemContext,
        history: Optional[List[Dict[str, str]]] = None
    ) -> AgentResponse:
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not found. Set it via `lta config set api_key <key>` or export OPENAI_API_KEY.")

        system_prompt = self._build_system_prompt(context)
        messages = [{"role": "system", "content": system_prompt}]

        if history:
            for h in history:
                messages.append({"role": "user", "content": h.get("user_intent", "")})
                messages.append({"role": "assistant", "content": json.dumps({"command": h.get("command", ""), "explanation": "Executed."})})

        messages.append({"role": "user", "content": f"{prompt}\n\nRespond strictly with JSON format: {{\"command\": \"...\", \"explanation\": \"...\"}}"})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }

        url = f"{self.endpoint}/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                choices = res_data.get("choices", [])
                if not choices:
                    raise ValueError("No response choices returned by OpenAI API.")
                raw_text = choices[0].get("message", {}).get("content", "")
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
            raise RuntimeError(f"OpenAI API HTTP {e.code}: {err_body}")
        except urllib.error.URLError as e:
            raise ConnectionError(f"Network error connecting to OpenAI: {e.reason}")

    def test_connection(self) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "OpenAI API key is missing."
        url = f"{self.endpoint}/models"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    return True, f"OpenAI API connected successfully. Using model: {self.model}"
                return False, f"Unexpected response code: {response.status}"
        except urllib.error.HTTPError as e:
            return False, f"OpenAI Authentication failed (HTTP {e.code})"
        except Exception as e:
            return False, f"Connection test failed: {str(e)}"
