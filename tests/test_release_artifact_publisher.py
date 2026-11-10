import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from publish_release_artifacts import publish

class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        data = b"release bytes"
        path = self.root / "dist" / "release" / "api.zip"
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        self.item = {"local_file": "dist/release/api.zip", "sha256_hex": sha,
                     "bytes": len(data), "s3_key": f"releases/sha256/{sha}/api.zip"}
        self.manifest = {"format": 1, "artifacts": {"api": self.item}}
    def test_offline_verification(self):
        self.assertEqual(publish(None, self.manifest, self.root, None)[0]["status"], "LOCAL_VERIFIED")
    def test_tampered_package_rejected(self):
        (self.root / self.item["local_file"]).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "integrity"):
            publish(None, self.manifest, self.root, None)
    def test_wrong_key_rejected(self):
        self.item["s3_key"] = "api/latest.zip"
        with self.assertRaisesRegex(ValueError, "immutable"):
            publish(None, self.manifest, self.root, None)
if __name__ == "__main__":
    unittest.main()
