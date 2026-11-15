"""PL-02 *design-contract* tests; NOT proof of a runtime permissions engine.

This file checks that our proposed policy matrix cannot accidentally encode
unattended privileged actions. It does not invoke Milo, any model, Windows,
filesystems outside the checked-in contract, or remote services.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest


CONTRACT = Path(__file__).resolve().parents[1] / "desktop" / "PL02_POLICY_CONTRACT.json"


def load_contract():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def design_decision(contract, *, action: str, mode: str, origin: str):
    """Static matrix lookup. This is intentionally NOT a runtime permission gate."""
    if origin == "untrusted_content":
        return "deny"
    if origin not in contract["origins"]:
        return "deny"
    if mode not in contract["modes"]:
        return "deny"
    matches = [x for x in contract["operations"] if x["id"] == action]
    if len(matches) != 1:
        return "deny"
    return matches[0]["decision"][mode]


class PL02DesignContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract()
        cls.operations = {x["id"]: x for x in cls.contract["operations"]}

    def test_contract_is_explicitly_unimplemented(self):
        self.assertEqual(self.contract["schema_version"], 1)
        self.assertEqual(self.contract["initial_mode"], "off")
        self.assertFalse(self.contract["implemented"])
        self.assertEqual(self.contract["status"], "design-only")
        self.assertTrue(self.contract["owner_confirmation_required"])

    def test_all_operations_have_explicit_safe_mode_matrix(self):
        self.assertEqual(len(self.operations), len(self.contract["operations"]))
        self.assertTrue(self.operations)
        for item in self.contract["operations"]:
            with self.subTest(operation=item["id"]):
                self.assertEqual(set(item["decision"]), {"off", "quiet", "active"})
                self.assertEqual(item["decision"]["off"], "deny")
                self.assertTrue(set(item["decision"].values()) <= {"deny", "review", "allow"})

    def test_no_privileged_or_external_write_is_automatically_allowed(self):
        protected = {
            "privileged_pc", "external_mutation", "memory_promote",
            "network_read", "privilege_escalation",
        }
        for item in self.contract["operations"]:
            if item["risk"] in protected:
                with self.subTest(operation=item["id"]):
                    self.assertNotEqual(item["decision"]["quiet"], "allow")
                    self.assertNotEqual(item["decision"]["active"], "allow")
        self.assertEqual(self.operations["external.mutate"]["decision"]["active"], "deny")
        self.assertEqual(self.operations["models.install"]["decision"]["active"], "deny")
        self.assertEqual(self.operations["permissions.modify"]["decision"]["quiet"], "deny")

    def test_untrusted_content_never_grants_authority(self):
        for item in self.contract["operations"]:
            for mode in self.contract["modes"]:
                with self.subTest(operation=item["id"], mode=mode):
                    self.assertEqual(design_decision(
                        self.contract, action=item["id"], mode=mode,
                        origin="untrusted_content"
                    ), "deny")

    def test_no_specialist_inherits_private_archive(self):
        scope = self.contract["actor_scope"]
        self.assertTrue(scope["milo"]["may_consult_private_archive_for_chat"])
        self.assertFalse(scope["milo"]["may_delegate_private_archive"])
        for bot_id in ("research", "webdev", "gamedev"):
            with self.subTest(bot_id=bot_id):
                self.assertFalse(scope[bot_id]["private_archive"])

    def test_event_contract_requires_provenance_and_idempotency(self):
        event = self.contract["event_contract"]
        for field in (
            "schema_version", "event_id", "timestamp_utc", "kind", "actor_id",
            "origin", "evidence_refs", "status", "summary", "idempotency_key",
        ):
            self.assertIn(field, event["required"])
        self.assertIn("task_interrupted", event["event_kinds"])
        self.assertIn("task_waiting_approval", event["event_kinds"])
        self.assertIn("never_success_without_verified_postcondition", event["data_rules"])
        self.assertIn("opaque_evidence_refs", event["data_rules"])

    def test_source_proposals_are_not_an_owner_approved_implementation(self):
        defaults = self.contract["defaults_proposed_not_approved"]
        self.assertEqual(defaults["task_concurrency"], 1)
        self.assertEqual(defaults["concurrent_gpu_inference"], 1)
        self.assertFalse(defaults["background_scheduling_enabled"])
        self.assertEqual(defaults["timezone"], "Australia/Hobart")
        self.assertGreater(len(defaults["owner_must_set"]), 0)

    def test_negative_scenario_matrix_including_untrusted_prompt_injection(self):
        cases = self.contract["negative_scenarios"]
        self.assertEqual({x["id"] for x in cases},
                         {f"NEG-{i:02d}" for i in range(1, 13)})
        for case in cases:
            with self.subTest(scenario=case["id"]):
                self.assertIn(case["action"], self.operations)
                self.assertIn(case["mode"], self.contract["modes"])
                self.assertEqual(design_decision(
                    self.contract, action=case["action"],
                    mode=case["mode"], origin=case["origin"]
                ), case["expected"])
        # NEG-06/09/10/12 describe outcome, retry, visuals and migration;
        # they need real integration tests later; a policy matrix alone
        # cannot establish those behavioural guarantees.


if __name__ == "__main__":
    unittest.main()
