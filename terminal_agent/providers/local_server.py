"""
Local OpenAI-compatible server provider (Offline).
Connects to llama.cpp server, LM Studio, vLLM, or LocalAI running locally.
Zero third-party dependencies (pure standard library urllib).
"""

import json
import urllib.request
import urllib.error
from typing import List, Dict, Tuple, Optional, Any
from terminal_agent.providers.base import BaseProvider, AgentResponse, parse_llm_json_response
from terminal_agent.core.context import SystemContext

class LocalServerProvider(BaseProvider):
    name = "local"
    is_offline = True

    def __init__(self, model: str = "default", config: Optional[Dict[str, Any]] = None):
        super().__init__(model=model, config=config)
        self.endpoint = self.config.get("endpoint", "http://localhost:8080/v1").rstrip("/")
        self.timeout = self.config.get("timeout", 60)
        self.api_key = self.config.get("api_key", "sk-local")

    def generate(
        self,
        prompt: str,
        context: SystemContext,
        history: Optional[List[Dict[str, str]]] = None
    ) -> AgentResponse:
        system_prompt = self._build_system_prompt(context)
        messages = [{"role": "system", "content": system_prompt}]

        if history:
            for h in history:
                messages.append({"role": "user", "content": h.get("user_intent", "")})
                messages.append({"role": "assistant", "content": json.dumps({"command": h.get("command", ""), "explanation": "Done."})})

        messages.append({"role": "user", "content": f"{prompt}\n\nRespond strictly with JSON format: {{\"command\": \"...\", \"explanation\": \"...\"}}"})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "stream": False
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
                    raise ValueError("No response choices returned by local server.")
                raw_text = choices[0].get("message", {}).get("content", "")
                cmd, exp = parse_llm_json_response(raw_text)
                return AgentResponse(
                    command=cmd,
                    explanation=exp,
                    provider_name=self.name,
                    model_name=self.model,
                    confidence=0.9,
                    raw_response=raw_text
                )
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"Failed to connect to local server at {self.endpoint}.\n"
                f"Ensure your local LLM server (llama.cpp / LM Studio / vLLM) is running. Error: {e.reason}"
            )
        except Exception as e:
            raise RuntimeError(f"Local server error: {str(e)}")

    def test_connection(self) -> Tuple[bool, str]:
        """Verify local server endpoint is responding."""
        url = f"{self.endpoint}/models"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=4) as response:
                data = json.loads(response.read().decode("utf-8"))
                models = [m.get("id") for m in data.get("data", []) if "id" in m]
                return True, f"Connected to local server at {self.endpoint}. Models found: {', '.join(models[:3]) or 'Ready'}"
        except urllib.error.URLError as e:
            return False, f"Could not connect to {self.endpoint}: {e.reason}"
        except Exception as e:
            return False, f"Local server check failed: {str(e)}"
