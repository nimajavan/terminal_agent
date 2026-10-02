"""
Ollama Provider for 100% offline and private local model execution.
Supports Llama 3, DeepSeek-Coder, Qwen2.5-Coder, Mistral, CodeLlama, etc.
Zero third-party dependencies (pure standard library urllib).
"""

import json
import urllib.request
import urllib.error
from terminal_agent.providers.transport import local_open
from typing import List, Dict, Tuple, Optional, Any
from terminal_agent.providers.base import BaseProvider, AgentResponse, parse_llm_json_response
from terminal_agent.core.context import SystemContext

class OllamaProvider(BaseProvider):
    name = "ollama"
    is_offline = True

    def __init__(self, model: str = "qwen2.5-coder:7b", config: Optional[Dict[str, Any]] = None):
        super().__init__(model=model, config=config)
        self.host = self.config.get("host", "http://localhost:11434").rstrip("/")
        self.timeout = self.config.get("timeout", 60)

    def generate(
        self,
        prompt: str,
        context: SystemContext,
        history: Optional[List[Dict[str, str]]] = None
    ) -> AgentResponse:
        system_prompt = self._build_system_prompt(context)
        history_text = ""
        if history:
            history_text = "\nPrevious session commands:\n" + "\n".join(
                [f"- Query: {h.get('user_intent')} -> Cmd: {h.get('command')} ({h.get('status')})" for h in history]
            )

        full_prompt = f"{system_prompt}\n{history_text}\n\nUser request: {prompt}\n\n{self.response_instruction()}"

        payload = {
            "model": self.model,
            "prompt": full_prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.1,
                "num_predict": self.config.get("max_output_tokens", 2048),
                "top_p": 0.9,
            }
        }

        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        try:
            with local_open(req, timeout=self.timeout) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                self.record_usage(res_data)
                raw_response = res_data.get("response", "")
                cmd, exp = parse_llm_json_response(raw_response)
                return AgentResponse(
                    command=cmd,
                    explanation=exp,
                    provider_name=self.name,
                    model_name=self.model,
                    confidence=0.95,
                    raw_response=raw_response
                )
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"Failed to connect to local Ollama at {self.host}.\n"
                f"Make sure Ollama is installed and running (`ollama serve`). Error: {e.reason}"
            )
        except Exception as e:
            raise RuntimeError(f"Ollama generation error: {str(e)}")

    def test_connection(self) -> Tuple[bool, str]:
        """Test if Ollama server is accessible and list models."""
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", headers={"Content-Type": "application/json"})
            with local_open(req, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                if self.model in models or any(m.startswith(self.model) for m in models):
                    return True, f"Connected to Ollama at {self.host}. Active model: {self.model} found."
                elif models:
                    return True, f"Connected to Ollama at {self.host}. Models installed: {', '.join(models[:5])}"
                return True, f"Connected to Ollama at {self.host}, but no models are installed yet. Run: `ollama pull {self.model}`"
        except urllib.error.URLError as e:
            return False, f"Cannot reach Ollama at {self.host} ({e.reason}). Start it with: `ollama serve`"
        except Exception as e:
            return False, f"Ollama connection check failed: {str(e)}"

    def get_available_models(self) -> List[str]:
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", headers={"Content-Type": "application/json"})
            with local_open(req, timeout=4) as response:
                data = json.loads(response.read().decode("utf-8"))
                return [m.get("name") for m in data.get("models", []) if "name" in m]
        except Exception:
            return [self.model]
