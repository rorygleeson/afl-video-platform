"""Offline safety checks for sanitized CloudFormation comparison."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from compare_live_templates import compare, parse_template


class LiveTemplateComparisonTests(unittest.TestCase):
    def test_identical_templates(self):
        template = parse_template("Resources:\n  A:\n    Type: AWS::S3::Bucket\n")
        result = compare(template, template)
        self.assertTrue(result["structurally_equal"])
        self.assertFalse(result["changed_resources"])

    def test_only_digests_leave_property_comparison(self):
        source = parse_template("Resources:\n  A:\n    Type: AWS::S3::Bucket\n    Properties:\n      BucketName: secret-source\n")
        live = parse_template("Resources:\n  A:\n    Type: AWS::S3::Bucket\n    Properties:\n      BucketName: secret-live\n")
        result = compare(source, live)
        self.assertFalse(result["structurally_equal"])
        self.assertEqual(["A"], [x["logical_id"] for x in result["changed_resources"]])
        self.assertNotIn("secret", str(result))

    def test_cfn_short_tags_normalized(self):
        source = parse_template("Resources:\n  A:\n    Type: AWS::S3::Bucket\n    Properties:\n      BucketName: !Sub '${AWS::StackName}-bucket'\n")
        deployed = parse_template('{"Resources":{"A":{"Type":"AWS::S3::Bucket","Properties":{"BucketName":{"Fn::Sub":"${AWS::StackName}-bucket"}}}}}')
        self.assertTrue(compare(source, deployed)["structurally_equal"])


if __name__ == "__main__":
    unittest.main()
