"""Offline contracts for the 10-second Google submission stagger.

Never calls AWS or Google, and never starts paid generation.
"""
import json
from pathlib import Path
import textwrap
import unittest

from scripts.deploy_google_stagger import (
    CLAIM_END, STAGGER_CLAIM_END, EXTRA_STATES, stagger_definition,
    stagger_template,
)

ROOT = Path(__file__).resolve().parents[1]


def definition_from_template(source):
    block = source.split("DefinitionString: !Sub |\n", 1)[1].split("\nOutputs:", 1)[0]
    return json.loads(textwrap.dedent(block))


class GoogleStaggerContracts(unittest.TestCase):
    def setUp(self):
        self.template = (ROOT / "infrastructure/app/google-video.yaml").read_text()
        self.current = definition_from_template(self.template)

    def test_scene_offsets_and_overlapping_generation(self):
        states = self.current["States"]
        self.assertEqual(states["Claim"]["Next"], "SubmissionSchedule")
        self.assertEqual(states["SubmissionSchedule"]["Default"], "Submit")
        routes = {r["StringEquals"]: r["Next"] for r in states["SubmissionSchedule"]["Choices"]}
        self.assertEqual(routes, {"scene2": "WaitScene2", "scene3": "WaitScene3"})
        self.assertEqual(states["WaitScene2"], {"Type": "Wait", "Seconds": 10, "Next": "Submit"})
        self.assertEqual(states["WaitScene3"], {"Type": "Wait", "Seconds": 20, "Next": "Submit"})
        self.assertEqual(states["Submit"]["Next"], "WaitForVideo")
        self.assertEqual(states["WaitForVideo"]["Seconds"], 20)

    def test_no_automatic_retries_or_extra_paid_calls(self):
        states = self.current["States"]
        # Only read-only polling may retry. Never resubmit a chargeable POST.
        self.assertEqual(set(name for name, state in states.items() if "Retry" in state),
                         {"PollVideo"})
        self.assertEqual(states["PollVideo"]["Retry"][0]["ErrorEquals"], ["States.TaskFailed"])
        self.assertNotIn("Retry", states["Submit"])
        self.assertEqual([name for name, state in states.items()
                          if state.get("Type") == "Task" and
                          state.get("Parameters", {}).get("Payload", {}).get("op") == "submit"],
                         ["Submit"])

    def test_exact_original_definition_transforms_to_committed_template(self):
        old_template = self.template.replace(STAGGER_CLAIM_END, CLAIM_END, 1).replace(EXTRA_STATES, "", 1)
        self.assertEqual(stagger_template(old_template), self.template)
        # The original one-time stagger transformer intentionally rejected every
        # retry policy. Test that historical transform independently of the new
        # read-only PollVideo recovery policy.
        old_states = definition_from_template(old_template)
        old_states["States"]["PollVideo"].pop("Retry", None)
        expected = json.loads(json.dumps(self.current))
        expected["States"]["PollVideo"].pop("Retry", None)
        self.assertEqual(stagger_definition(json.dumps(old_states)), expected)

    def test_original_tasks_are_unchanged(self):
        states = self.current["States"]
        for name in ("PaidGate", "Claim", "Submit", "PollVideo", "Finalize"):
            self.assertEqual(states[name]["Type"], "Task")
        self.assertIn("$$.Execution.Name", states["Submit"]["Parameters"]["Payload"]["requestId.$"])
        self.assertEqual(states["PollVideo"]["Parameters"]["Payload"]["op"], "poll")


if __name__ == "__main__":
    unittest.main()
