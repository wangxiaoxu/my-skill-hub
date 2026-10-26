import json
import subprocess
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "resume_lint.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def invoke(*args, **kwargs):
    command = [sys.executable, str(SCRIPT), *args]
    return subprocess.run(
        command, capture_output=True, text=True, **kwargs
    )


class ResumeLintTest(unittest.TestCase):
    def test_broken_resume_hits_at_least_four_categories(self):
        result = invoke(str(FIXTURES / "resume_broken.txt"), "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        codes = {item["code"] for item in payload["issues"]}
        for expected in (
            "missing_contact",
            "no_metrics",
            "weak_verb",
            "long_paragraph",
            "date_format_inconsistent",
        ):
            self.assertIn(expected, codes)
        self.assertGreaterEqual(len(codes), 4)

        weak = [item for item in payload["issues"] if item["code"] == "weak_verb"]
        self.assertGreaterEqual(len(weak), 2)
        self.assertTrue(all(item["line"] > 0 for item in weak))

    def test_good_resume_passes(self):
        result = invoke(str(FIXTURES / "resume_good.txt"), "--json")
        self.assertEqual(result.returncode, 0, result.stdout)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["issues"], [])
        self.assertEqual(payload["issueCount"], 0)

    def test_placeholder_detected_from_stdin(self):
        text = (
            "张三\n"
            "电话：13800000000 邮箱：z@example.com\n"
            "- 主导接口优化，P99 下降 40%，覆盖 3 个业务线。\n"
            "- 主导 <待补：QPS> 的压测。\n"
        )
        result = invoke("-", "--json", input=text)
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        codes = [item["code"] for item in payload["issues"]]
        self.assertEqual(codes, ["placeholder"])
        self.assertEqual(payload["issues"][0]["line"], 4)

    def test_missing_file_exits_2(self):
        result = invoke(str(FIXTURES / "does-not-exist.txt"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("无法读取", result.stderr)

    def test_help(self):
        result = invoke("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
