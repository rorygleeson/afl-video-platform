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

    def test_current_video_has_immediate_handoff_without_stagger(self):
        states = self.current["States"]
        self.assertEqual(states["Claim"]["Next"], "Submit")
        for old in ("SubmissionSchedule", "WaitScene2", "WaitScene3"):
            self.assertNotIn(old, states)
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

    def test_legacy_stagger_transform_still_produces_stagger_states(self):
        # Historical deploy helper is retained for rollback analysis; it is
        # not the current production definition.
        old_states = json.loads(json.dumps(self.current))
        old_states["States"]["PollVideo"].pop("Retry", None)
        staggered = stagger_definition(json.dumps(old_states))
        self.assertEqual(staggered["States"]["Claim"]["Next"], "SubmissionSchedule")
        self.assertEqual(staggered["States"]["WaitScene2"]["Seconds"], 10)
        self.assertEqual(staggered["States"]["WaitScene3"]["Seconds"], 20)

    def test_original_tasks_are_unchanged(self):
        states = self.current["States"]
        for name in ("PaidGate", "Claim", "Submit", "PollVideo", "Finalize"):
            self.assertEqual(states[name]["Type"], "Task")
        self.assertIn("$$.Execution.Name", states["Submit"]["Parameters"]["Payload"]["requestId.$"])
        self.assertEqual(states["PollVideo"]["Parameters"]["Payload"]["op"], "poll")


if __name__ == "__main__":
    unittest.main()
