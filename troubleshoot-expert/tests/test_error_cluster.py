import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "error_cluster.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(ROOT / "scripts"))
import error_cluster  # noqa: E402


def run(*args, **kwargs):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        capture_output=True,
        text=True,
        **kwargs
    )


class NormalizeTest(unittest.TestCase):
    def test_replaces_variable_parts(self):
        message = (
            "request 550e8400-e29b-41d4-a716-446655440000 at "
            "2026-10-26T10:00:00Z wrote /var/log/app/db.sqlite "
            "from 10.0.0.1:5432 with 0xdeadbeef after 42 retries"
        )
        normalized = error_cluster.normalize(message)
        self.assertIn("<UUID>", normalized)
        self.assertIn("<TS>", normalized)
        self.assertIn("<PATH>", normalized)
        self.assertIn("<IP>", normalized)
        self.assertIn("<N>", normalized)
        self.assertNotIn("550e8400", normalized)
        self.assertNotIn("deadbeef", normalized)

    def test_same_shape_after_normalization(self):
        left = error_cluster.normalize("failed to open /tmp/a.txt after 3 tries")
        right = error_cluster.normalize("failed to open /tmp/b.txt after 9 tries")
        self.assertEqual(left, right)


class ClusterCliTest(unittest.TestCase):
    def test_top_cluster_and_first_last_seen(self):
        result = run(str(FIXTURES / "errors.log"), "--top", "3", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["totalLines"], 7)
        self.assertEqual(payload["clusterCount"], 3)
        top = payload["clusters"][0]
        self.assertEqual(top["count"], 3)
        self.assertEqual(top["first_seen"], "2026-10-26T10:00:00")
        self.assertEqual(top["last_seen"], "2026-10-26T10:00:06")
        self.assertIn("<IP>", top["normalized"])
        self.assertIn("connection refused", top["sample"])

    def test_json_log_message_field_merges_with_plain_line(self):
        result = run(str(FIXTURES / "errors.log"), "--top", "10", "--json")
        payload = json.loads(result.stdout)
        timeout_cluster = [
            item
            for item in payload["clusters"]
            if "timeout calling" in item["normalized"]
        ]
        self.assertEqual(len(timeout_cluster), 1)
        self.assertEqual(timeout_cluster[0]["count"], 2)

    def test_stdin_input(self):
        text = (
            "ERROR disk full on /data/a\n"
            "ERROR disk full on /data/b\n"
        )
        result = run("-", "--json", input=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["clusters"][0]["count"], 2)

    def test_missing_file_exits_2(self):
        result = run(str(FIXTURES / "missing.log"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("无法读取", result.stderr)

    def test_empty_input(self):
        result = run("-", "--json", input="\n\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["clusters"], [])

    def test_help(self):
        result = run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
