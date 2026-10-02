"""Unit tests for modular providers."""
import unittest
from terminal_agent.providers.base import parse_llm_json_response
from terminal_agent.providers.rule_based import RuleBasedProvider
from terminal_agent.providers.factory import get_provider, PROVIDER_REGISTRY
from terminal_agent.core.context import get_system_context

class TestProviders(unittest.TestCase):
    def test_json_parsing(self):
        # 1. Clean JSON
        raw1 = '{"command": "df -h", "explanation": "Checks disk space"}'
        cmd, exp = parse_llm_json_response(raw1)
        self.assertEqual(cmd, "df -h")
        self.assertEqual(exp, "Checks disk space")

        # 2. Markdown fenced JSON
        raw2 = '```json\n{"command": "free -m", "explanation": "Checks RAM"}\n```'
        cmd, exp = parse_llm_json_response(raw2)
        self.assertEqual(cmd, "free -m")
        self.assertEqual(exp, "Checks RAM")

        # 3. Plain bash fence
        raw3 = '```bash\nls -lh /var\n```\nLists files in var'
        cmd, exp = parse_llm_json_response(raw3)
        self.assertEqual(cmd, "ls -lh /var")

    def test_rule_based_provider(self):
        ctx = get_system_context()
        provider = RuleBasedProvider()

        res1 = provider.generate("show disk usage", ctx)
        self.assertEqual(res1.command, "df -h")

        res2 = provider.generate("show free memory", ctx)
        self.assertEqual(res2.command, "free -h")

        res3 = provider.generate("open ports", ctx)
        self.assertIn("ss -tulpn", res3.command)

        # Persian support
        res4 = provider.generate("فضای دیسک", ctx)
        self.assertEqual(res4.command, "df -h")

    def test_provider_registry(self):
        expected = ["ollama", "local", "rule_based", "openai", "anthropic", "gemini", "groq", "openrouter"]
        for p in expected:
            self.assertIn(p, PROVIDER_REGISTRY)

if __name__ == "__main__":
    unittest.main()
