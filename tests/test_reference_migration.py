"""Offline contracts for the one-time stable-reference migration preview."""
import copy
import unittest
from pathlib import Path

from scripts.preview_reference_migration import (
    EXPECTED, parse_template, validate_exact_template_transition, at,
)


class StableReferenceMigrationTests(unittest.TestCase):
    def old_from_source(self, label, path):
        source = parse_template(Path(path).read_text())
        previous = copy.deepcopy(source)
        for segments, (before, after) in EXPECTED[label].items():
            parent = at(previous, segments[:-1])
            if label == "watchdog" and segments[-1] == "DefinitionString":
                text = parent[segments[-1]]["Fn::Sub"]
                needle = "arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:afl-app-dev-handoff-watchdog"
                self.assertEqual(text.count(needle), 1)
                parent[segments[-1]] = {"Fn::Sub": text.replace(needle, "${WatchdogLambda.Arn}")}
            else:
                self.assertEqual(parent[segments[-1]], after)
                parent[segments[-1]] = before
        return previous, source

    def test_backend_exact_references_only(self):
        previous, source = self.old_from_source("backend", "infrastructure/app/backend.yaml")
        self.assertTrue(validate_exact_template_transition(previous, source, "backend"))
        source["Resources"]["ProxyAny"]["Properties"]["ApiKeyRequired"] = False
        with self.assertRaisesRegex(ValueError, "Unapproved"):
            validate_exact_template_transition(previous, source, "backend")

    def test_watchdog_exact_references_only(self):
        previous, source = self.old_from_source("watchdog", "infrastructure/app/handoff-watchdog.yaml")
        self.assertTrue(validate_exact_template_transition(previous, source, "watchdog"))
        source["Resources"]["EveryMinute"]["Properties"]["State"] = "DISABLED"
        with self.assertRaisesRegex(ValueError, "Unapproved"):
            validate_exact_template_transition(previous, source, "watchdog")

    def test_unexpected_live_reference_refused(self):
        previous, source = self.old_from_source("watchdog", "infrastructure/app/handoff-watchdog.yaml")
        previous["Resources"]["InvokeFromEventBridge"]["Properties"]["SourceArn"] = "unexpected"
        with self.assertRaisesRegex(ValueError, "Unexpected live template"):
            validate_exact_template_transition(previous, source, "watchdog")


if __name__ == "__main__":
    unittest.main()
