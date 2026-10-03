"""
Google Gemini online provider using the generateContent API.
Pure standard library implementation.
"""

import os
import json
import urllib.request
import urllib.error
from typing import List, Dict, Tuple, Optional, Any
from terminal_agent.providers.base import BaseProvider, AgentResponse, parse_llm_json_response
from terminal_agent.core.context import SystemContext
from terminal_agent.core.privacy import redact
from terminal_agent.providers.errors import http_failure, network_failure, retry_after_seconds
from terminal_agent.providers.transport import read_json_response



class GeminiProvider(BaseProvider):
    name = "gemini"
    is_offline = False

    def __init__(self, model: str = "gemini-3.8-flash", config: Optional[Dict[str, Any]] = None):
        super().__init__(model=model, config=config)
        self.api_key = self.config.get("api_key") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
        self.timeout = self.config.get("timeout", 45)

    def generate(
        self,
        prompt: str,
        context: SystemContext,
        history: Optional[List[Dict[str, str]]] = None
    ) -> AgentResponse:
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not found. Set it in config or export GEMINI_API_KEY.")

        system_instruction = self._build_system_prompt(context)
        contents = []

        if history:
            for h in history:
                contents.append({"role": "user", "parts": [{"text": h.get("user_intent", "")}]})
                contents.append({"role": "model", "parts": [{"text": json.dumps({"command": h.get("command", ""), "explanation": "Done."})}]})

        contents.append({"role": "user", "parts": [{"text": f"{prompt}\n\n{self.response_instruction()}"}]})

        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": self.config.get("max_output_tokens", 2048),
                "response_mime_type": "application/json"
            }
        }

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                res_data = read_json_response(response)
                self.record_usage(res_data)
                candidates = res_data.get("candidates", [])
                if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
                    raise ValueError("No response returned from Gemini API.")
                content = candidates[0].get("content")
                if not isinstance(content, dict):
                    raise ValueError("Gemini response content must be an object")
                parts = content.get("parts")
                if not isinstance(parts, list) or any(not isinstance(p, dict) or not isinstance(p.get("text", ""), str) for p in parts):
                    raise ValueError("Gemini response parts must contain text")
                raw_text = "".join(p.get("text", "") for p in parts)
                cmd, exp = parse_llm_json_response(raw_text)
                return AgentResponse(
                    command=cmd,
                    explanation=exp,
                    provider_name=self.name,
                    model_name=self.model,
                    confidence=0.97,
                    raw_response=raw_text
                )
        except urllib.error.HTTPError as e:
            raise http_failure(self.name, e) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise network_failure(self.name, e) from e

    def test_connection(self) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "Gemini API key is missing."
        return True, f"Google Gemini provider configured for model: {self.model}"
