"""
Base interface and dataclasses for AI providers (online and offline).
"""

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional, Any
from terminal_agent.core.context import SystemContext

@dataclass
class AgentResponse:
    command: str
    explanation: str
    provider_name: str
    model_name: str
    confidence: float = 1.0
    raw_response: str = ""

    def clean_command(self) -> str:
        """Strip backticks, leading comments, or trailing quotes."""
        cmd = self.command.strip()
        if cmd.startswith("```bash"):
            cmd = cmd[7:]
        elif cmd.startswith("```sh"):
            cmd = cmd[5:]
        elif cmd.startswith("```"):
            cmd = cmd[3:]
        if cmd.endswith("```"):
            cmd = cmd[:-3]
        return cmd.strip()


def parse_llm_json_response(raw_text: str) -> Tuple[str, str]:
    """
    Robustly extract 'command' and 'explanation' from model output,
    handling JSON markdown codeblocks, raw JSON, or plain text fallback.
    """
    text = raw_text.strip()
    if not text:
        return "", "No output returned from AI model."

    # 1. Try direct json parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return str(data.get("command", "")).strip(), str(data.get("explanation", "")).strip()
    except Exception:
        pass

    # 2. Try extracting from ```json ... ``` code fence
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            data = json.loads(fence_match.group(1))
            if isinstance(data, dict):
                return str(data.get("command", "")).strip(), str(data.get("explanation", "")).strip()
        except Exception:
            pass

    # 3. Try finding any JSON object {...}
    json_match = re.search(r"\{[^{}]*\"command\"[^{}]*\}", text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            if isinstance(data, dict):
                return str(data.get("command", "")).strip(), str(data.get("explanation", "")).strip()
        except Exception:
            pass

    # 4. Fallback: extract command from bash fence or take single line
    bash_fence = re.search(r"```(?:bash|sh)?\s*(.*?)\s*```", text, re.DOTALL)
    if bash_fence:
        return bash_fence.group(1).strip(), "Command extracted from AI code block."

    # If the text has no codeblock, take the first non-empty line as command
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        cmd = lines[0].lstrip("$ ").strip()
        exp = " ".join(lines[1:]) if len(lines) > 1 else "Generated terminal command."
        return cmd, exp

    return text, "Generated command."


class BaseProvider(ABC):
    """Abstract class for all LLM providers."""

    name: str = "base"
    is_offline: bool = False

    def __init__(self, model: str, config: Optional[Dict[str, Any]] = None):
        self.model = model
        self.config = config or {}

    @abstractmethod
    def generate(
        self,
        prompt: str,
        context: SystemContext,
        history: Optional[List[Dict[str, str]]] = None
    ) -> AgentResponse:
        """Generate command and explanation from natural language prompt."""
        pass

    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """Test whether the provider endpoint / credentials are reachable."""
        pass

    def get_available_models(self) -> List[str]:
        """List available models for this provider."""
        return [self.model]

    def _build_system_prompt(self, context: SystemContext) -> str:
        """Build a strict, structured system prompt for Linux terminal command generation."""
        return (
            "You are an expert Linux terminal assistant. Your job is to convert natural language instructions "
            "into precise, reliable, and secure shell commands.\n\n"
            f"Target System Context:\n{context.system_prompt_context()}\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. Output ONLY a valid JSON object with keys 'command' and 'explanation'.\n"
            "2. 'command' must be a valid, executable Bash one-liner or pipeline tailored to the detected OS, shell, and package manager.\n"
            "3. 'explanation' must be a concise (1-2 sentences) explanation in English or the prompt's language.\n"
            "4. NEVER output markdown code fences outside JSON. Never write introductory chat.\n"
            "5. If a command requires superuser privileges and the user is not root, prefix with 'sudo'.\n"
            "6. Example valid response format:\n"
            "{\n"
            "  \"command\": \"find /var/log -type f -size +100M\",\n"
            "  \"explanation\": \"Searches for all files in /var/log larger than 100 megabytes.\"\n"
            "}"
        )
