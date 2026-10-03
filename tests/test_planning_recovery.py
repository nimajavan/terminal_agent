"""Model labels can be repaired; dependencies, actions and safety cannot be guessed."""
import copy
import json
import os
import unittest
from unittest.mock import patch
from terminal_agent.core.context import get_system_context
from terminal_agent.core.planning import parse_plan, normalize_generated_ids, plan_json_schema
from terminal_agent.core.tools import ToolRunner, validate_action
from terminal_agent.providers.base import AgentResponse
from terminal_agent.providers.errors import InvalidModelPlan
from terminal_agent.providers.factory import get_provider
from terminal_agent.providers.router import ModelRouter
from test_agent import TempCase, file_plan


class IDRecovery(unittest.TestCase):
    def test_numeric_ids_and_references_are_mapped_without_action_changes(self):
        plan = file_plan()
        plan["steps"][0]["id"] = 1
        plan["steps"][1]["id"] = 2
        plan["steps"][1]["depends_on"] = [1]
        original = copy.deepcopy(plan)
        result = parse_plan(json.dumps(plan), generated=True)
        self.assertEqual(result["steps"][1]["depends_on"], [result["steps"][0]["id"]])
        self.assertEqual(result["steps"][1]["args"], original["steps"][1]["args"])
        self.assertEqual(plan, original)

    def test_invalid_unique_label_is_mapped(self):
        plan = file_plan()
        plan["steps"][0]["id"] = "inspect config!"
        plan["steps"][1]["depends_on"] = ["inspect config!"]
        result = parse_plan(json.dumps(plan), generated=True)
        self.assertEqual(result["steps"][1]["depends_on"], [result["steps"][0]["id"]])

    def test_duplicate_unreferenced_labels_can_be_renamed(self):
        plan = file_plan()
        plan["steps"][1]["id"] = "read"
        plan["steps"][1]["depends_on"] = []
        result = parse_plan(json.dumps(plan), generated=True)
        self.assertEqual(len({s["id"] for s in result["steps"]}), 2)

    def test_duplicate_referenced_labels_are_ambiguous(self):
        plan = file_plan()
        plan["steps"][1]["id"] = "read"
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            parse_plan(json.dumps(plan), generated=True)

    def test_forward_dependencies_still_fail(self):
        plan = file_plan()
        plan["steps"][0]["depends_on"] = ["write"]
        with self.assertRaises(ValueError):
            parse_plan(json.dumps(plan), generated=True)

    def test_missing_labels_can_be_filled_without_dependencies(self):
        plan = file_plan()
        del plan["steps"][0]["id"]
        plan["steps"][1]["depends_on"] = []
        result = parse_plan(json.dumps(plan), generated=True)
        self.assertIsInstance(result["steps"][0]["id"], str)

    def test_reserved_valid_labels_are_preserved(self):
        plan = file_plan()
        plan["steps"][0]["id"] = None
        plan["steps"][1]["id"] = "step_1"
        plan["steps"][1]["depends_on"] = []
        result = parse_plan(json.dumps(plan), generated=True)
        self.assertEqual(result["steps"][1]["id"], "step_1")
        self.assertNotEqual(result["steps"][0]["id"], "step_1")

    def test_manual_plan_import_remains_strict(self):
        plan = file_plan()
        plan["steps"][0]["id"] = 1
        with self.assertRaises(ValueError):
            parse_plan(json.dumps(plan))

    def test_normalization_does_not_remove_required_verification(self):
        plan = file_plan()
        plan["steps"][1]["id"] = 2
        plan["steps"][1]["verify"] = []
        with self.assertRaisesRegex(ValueError, "verification"):
            parse_plan(json.dumps(plan), generated=True)

    def test_schema_is_bounded_and_constrains_step_labels(self):
        schema = plan_json_schema(3)
        self.assertEqual(schema["properties"]["steps"]["maxItems"], 3)
        for branch in schema["properties"]["steps"]["items"]["oneOf"]:
            self.assertIn("pattern", branch["properties"]["id"])
            self.assertFalse(branch["additionalProperties"])
        self.assertLess(len(json.dumps(schema)), 12000)


class ProviderRecovery(unittest.TestCase):
    def router(self, config=None):
        return ModelRouter(get_provider("ollama", model="qwen2.5-coder:1.5b"), {"route_simple": False, **(config or {})})

    def response(self, plan):
        raw = json.dumps(plan) if not isinstance(plan, str) else plan
        return AgentResponse("", "", "ollama", "test", raw_response=raw)

    def test_ollama_sends_schema_in_format(self):
        provider = get_provider("ollama")
        with patch("terminal_agent.providers.ollama.local_open") as transport:
            transport.return_value.__enter__.return_value.read.return_value = json.dumps({"response": json.dumps(file_plan())}).encode()
            provider.generate_plan("repair config", get_system_context(), max_steps=4)
        payload = json.loads(transport.call_args[0][0].data)
        self.assertIsInstance(payload["format"], dict)
        self.assertEqual(payload["format"]["properties"]["steps"]["maxItems"], 4)

    def test_one_correction_then_success_is_accounted(self):
        router = self.router()
        invalid = file_plan()
        invalid["steps"][1]["id"] = "read"
        with patch.object(router.provider, "generate", side_effect=[self.response(invalid), self.response(file_plan())]) as model:
            plan = router.generate("repair config", get_system_context(), planning=True)
        self.assertEqual(model.call_count, 2)
        self.assertEqual([r["status"] for r in router.records], ["failed", "completed"])
        self.assertIn("ambiguous", model.call_args_list[1].args[0])
        self.assertEqual(plan["steps"][1]["id"], "write")
        self.assertFalse(router.provider._planning)

    def test_invalid_correction_stops_after_two_calls(self):
        router = self.router()
        with patch.object(router.provider, "generate", return_value=self.response("not JSON")) as model:
            with self.assertRaises(InvalidModelPlan):
                router.generate("repair config", get_system_context(), planning=True)
        self.assertEqual(model.call_count, 2)

    def test_call_limit_applies_to_correction(self):
        router = self.router({"max_model_calls": 1})
        with patch.object(router.provider, "generate", return_value=self.response("not JSON")) as model:
            with self.assertRaises(InvalidModelPlan):
                router.generate("repair config", get_system_context(), planning=True)
        model.assert_called_once()

    def test_cloud_budget_applies_to_correction(self):
        provider = get_provider("openai", model="test", config={"openai": {"api_key": "mock-key"}})
        router = ModelRouter(provider, {"route_simple": False, "budget_usd": .025, "pricing": {"openai/test": {"input": 1, "output": 1}}})
        with patch.object(provider, "generate", return_value=self.response("not JSON")) as model:
            with self.assertRaisesRegex(ValueError, "budget"):
                router.generate("repair config", get_system_context(), planning=True)
        model.assert_called_once()

    def test_evidence_review_normalizes_proposed_plan_ids(self):
        provider = get_provider("ollama")
        plan = file_plan()
        plan["steps"][0]["id"] = 1
        plan["steps"][1]["depends_on"] = [1]
        raw = json.dumps({"conclusion": "Needs repair", "goal_met": False, "next_plan": plan})
        with patch.object(provider, "generate", return_value=self.response(raw)):
            review = provider.review_evidence("review results", get_system_context())
        self.assertEqual(review["next_plan"]["steps"][1]["depends_on"], [review["next_plan"]["steps"][0]["id"]])

    def test_invalid_dependency_does_not_bypass_final_workflow_validation(self):
        router = self.router()
        plan = file_plan()
        plan["steps"][1]["verify"] = []
        with patch.object(router.provider, "generate", return_value=self.response(plan)) as model:
            with self.assertRaises(InvalidModelPlan):
                router.generate("repair config", get_system_context(), planning=True)
        self.assertEqual(model.call_count, 2)

    def test_corrective_prompt_filters_credentials(self):
        router = self.router()
        with patch.object(router.provider, "generate", side_effect=[self.response("password=private-example"), self.response(file_plan())]) as model:
            router.generate("repair config", get_system_context(), planning=True)
        self.assertNotIn("private-example", model.call_args_list[1].args[0])

    def test_exact_requested_task_can_plan_offline(self):
        router = self.router({"route_simple": True})
        with patch.object(router.provider, "generate") as model:
            plan = router.generate("Monitors live network traffic", get_system_context(), planning=True)
        model.assert_not_called()
        self.assertEqual(plan["steps"][0]["tool"], "network_traffic")

    def test_compound_request_not_replaced_by_simple_monitor(self):
        router = self.router({"route_simple": True})
        with patch.object(router.provider, "generate", return_value=self.response(file_plan())) as model:
            router.generate("Monitor network traffic and fix the firewall", get_system_context(), planning=True)
        model.assert_called_once()


class TrafficTests(TempCase):
    def test_live_rates_with_deterministic_counters(self):
        runner = ToolRunner(self.project, self.base / "data", stream=False)
        clock = [0.0]
        def wait(delay):
            clock[0] += delay
        with patch("terminal_agent.core.tools.time.monotonic", side_effect=lambda: clock[0]), patch("terminal_agent.core.tools.network_wait", side_effect=wait), patch("terminal_agent.core.tools.read_network_counters", side_effect=[{"eth0": (0, 0)}, {"eth0": (1024, 2048)}, {"eth0": (3072, 5120)}]):
            result = runner.run({"tool": "network_traffic", "args": {"seconds": 2, "interval": 1}})
        self.assertTrue(result.succeeded)
        self.assertIn("1.0s  eth0  1.00  2.00", result.stdout)
        self.assertIn("2.0s  eth0  2.00  3.00", result.stdout)
        self.assertEqual(clock[0], 2)

    def test_bounds_and_types(self):
        for args in [{"seconds": 0, "interval": 1}, {"seconds": 61, "interval": 1}, {"seconds": 1, "interval": 2}, {"seconds": True, "interval": 1}]:
            with self.assertRaises(ValueError):
                validate_action({"tool": "network_traffic", "args": args})

    def test_duration_cannot_exceed_timeout(self):
        runner = ToolRunner(self.project, self.base / "data", timeout=1, stream=False)
        self.assertFalse(runner.run({"tool": "network_traffic", "args": {"seconds": 2, "interval": 1}}).succeeded)

    @unittest.skipUnless(os.name == "posix", "Requires Linux interface counters")
    def test_real_linux_interface_sampling(self):
        runner = ToolRunner(self.project, self.base / "data", stream=False)
        result = runner.run({"tool": "network_traffic", "args": {"seconds": 1, "interval": 1}})
        self.assertTrue(result.succeeded, result.stderr)
        self.assertIn("RX KiB/s", result.stdout)
