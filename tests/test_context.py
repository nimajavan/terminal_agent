"""Unit tests for context detection and executor."""
import unittest
import sys
from terminal_agent.core.context import get_system_context
from terminal_agent.core.executor import CommandExecutor

class TestContextAndExecutor(unittest.TestCase):
    def test_context_detection(self):
        ctx = get_system_context()
        self.assertIsNotNone(ctx.distro)
        self.assertIsNotNone(ctx.shell)
        self.assertIsNotNone(ctx.arch)
        self.assertTrue(len(ctx.summary()) > 0)
        self.assertTrue(len(ctx.system_prompt_context()) > 0)

    def test_executor_success(self):
        executor = CommandExecutor(default_timeout=10)
        res = executor.execute([sys.executable, "-c", "print('lta_test')"])
        self.assertTrue(res.succeeded)
        self.assertEqual(res.exit_code, 0)
        self.assertIn("lta_test", res.stdout)
        self.assertFalse(res.timed_out)

    def test_executor_failure(self):
        executor = CommandExecutor(default_timeout=10)
        res = executor.execute("non_existing_binary_xyz_123 2>&1")
        self.assertFalse(res.succeeded)
        self.assertNotEqual(res.exit_code, 0)

if __name__ == "__main__":
    unittest.main()
