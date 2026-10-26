import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "plan_check.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run(*args, **kwargs):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        capture_output=True,
        text=True,
        **kwargs
    )


class PlanCheckTest(unittest.TestCase):
    def test_good_plan_passes(self):
        result = run(str(FIXTURES / "plan_good.md"), "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["issues"], [])
        self.assertEqual(len(payload["days"]), 7)
        buffer_days = [day for day in payload["days"] if day["isBuffer"]]
        self.assertEqual(len(buffer_days), 1)

    def test_bad_plan_reports_multiple_categories(self):
        result = run(str(FIXTURES / "plan_bad.md"), "--json")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        codes = {item["code"] for item in payload["issues"]}
        for expected in (
            "too_many_tasks",
            "block_too_long",
            "missing_duration",
            "missing_acceptance",
            "missing_buffer",
            "missing_review",
        ):
            self.assertIn(expected, codes)
        self.assertGreaterEqual(len(codes), 4)

    def test_no_day_structure(self):
        result = run("-", "--json", input="# 一些随意的笔记\n今天要努力。\n")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["issues"][0]["code"], "no_day_structure")

    def test_custom_task_limit(self):
        result = run(str(FIXTURES / "plan_good.md"), "--max-tasks", "1", "--json")
        self.assertEqual(result.returncode, 1)
        codes = [item["code"] for item in json.loads(result.stdout)["issues"]]
        self.assertIn("too_many_tasks", codes)

    def test_missing_file_exits_2(self):
        result = run(str(FIXTURES / "missing-plan.md"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("无法读取", result.stderr)

    def test_help(self):
        result = run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
