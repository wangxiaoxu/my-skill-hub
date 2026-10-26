import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "log_timeline.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run(*args, **kwargs):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        capture_output=True,
        text=True,
        **kwargs
    )


class LogTimelineTest(unittest.TestCase):
    def test_merge_order_and_untimed_marking(self):
        result = run(
            str(FIXTURES / "app.log"),
            str(FIXTURES / "access.log"),
            str(FIXTURES / "syslog.log"),
            "--year",
            "2026",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        records = payload["records"]
        stamps = [item["timestamp"] for item in records]
        self.assertEqual(stamps, sorted(stamps))

        texts = [item["text"] for item in records]
        self.assertTrue(texts[0].endswith("INFO app started version=1.2.3"))
        self.assertIn('"GET /health HTTP/1.1" 200 3', texts[2])
        self.assertEqual(texts[3], "Oct 26 10:00:03 host app: heartbeat ok")

        untimed = [item for item in records if item["untimed"]]
        self.assertEqual(len(untimed), 1)
        self.assertEqual(untimed[0]["text"], "    at repo.get (repo.py:31)")
        self.assertEqual(untimed[0]["timestamp"], "2026-10-26T10:00:05.100000Z")

    def test_since_until_window(self):
        result = run(
            str(FIXTURES / "app.log"),
            "--since",
            "2026-10-26T10:00:05",
            "--until",
            "2026-10-26T10:00:07",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        records = json.loads(result.stdout)["records"]
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["timestamp"], "2026-10-26T10:00:05.100000Z")
        self.assertTrue(records[1]["untimed"])
        self.assertIn("WARN retrying", records[2]["text"])

    def test_until_date_only_includes_whole_day(self):
        result = run(
            str(FIXTURES / "app.log"),
            "--until",
            "2026-10-26",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        records = json.loads(result.stdout)["records"]
        self.assertEqual(len(records), 6)

    def test_grep_filters_lines(self):
        result = run(
            str(FIXTURES / "app.log"),
            "--grep",
            "refused",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        records = json.loads(result.stdout)["records"]
        self.assertEqual(len(records), 2)
        self.assertFalse(any(item["untimed"] for item in records))

    def test_missing_file_exits_2(self):
        result = run(str(FIXTURES / "does-not-exist.log"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("文件不存在", result.stderr)

    def test_invalid_regex_exits_2(self):
        result = run(str(FIXTURES / "app.log"), "--grep", "[")
        self.assertEqual(result.returncode, 2)

    def test_help(self):
        result = run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(
            "usage" in result.stdout.lower() or "用法" in result.stdout
        )


if __name__ == "__main__":
    unittest.main()
