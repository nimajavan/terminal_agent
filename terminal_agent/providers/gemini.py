"""
Google Gemini Online Provider (Gemini 1.5 Flash, Gemini 1.5 Pro).
Pure standard library implementation.
"""

import os
import json
import urllib.request
import urllib.error
from typing import List, Dict, Tuple, Optional, Any
from terminal_agent.providers.base import BaseProvider, AgentResponse, parse_llm_json_response
from terminal_agent.core.context import SystemContext

class GeminiProvider(BaseProvider):
    name = "gemini"
    is_offline = False

    def __init__(self, model: str = "gemini-1.5-flash", config: Optional[Dict[str, Any]] = None):
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

        contents.append({"role": "user", "parts": [{"text": f"{prompt}\n\nRespond strictly with JSON containing 'command' and 'explanation'."}]})

        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json"
            }
        }

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                candidates = res_data.get("candidates", [])
                if not candidates:
                    raise ValueError("No response returned from Gemini API.")
                parts = candidates[0].get("content", {}).get("parts", [])
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
            err_body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Gemini API HTTP {e.code}: {err_body}")
        except urllib.error.URLError as e:
            raise ConnectionError(f"Network error connecting to Gemini: {e.reason}")

    def test_connection(self) -> Tuple[bool, str]:
        if not self.api_key:
            return False, "Gemini API key is missing."
        return True, f"Google Gemini provider configured for model: {self.model}"
