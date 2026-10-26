import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "jd_match.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run(*args, **kwargs):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        capture_output=True,
        text=True,
        **kwargs
    )


def match(jd, resume):
    result = run("--jd", str(FIXTURES / jd), "--resume", str(FIXTURES / resume), "--json")
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class JdMatchTest(unittest.TestCase):
    def test_strong_resume_matches_backend_jd(self):
        payload = match("jd_backend.txt", "resume_strong.txt")
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["coverage"]["required"]["percent"], 75.0)
        self.assertGreaterEqual(payload["overall"], 70.0)
        self.assertEqual(payload["verdict"], "可投")
        gaps = [item.lower() for item in payload["gaps"]["required"]]
        self.assertNotIn("kubernetes", gaps)
        self.assertNotIn("redis", gaps)
        self.assertIn("中间件", payload["gaps"]["required"])

    def test_weak_resume_has_large_gap(self):
        strong = match("jd_backend.txt", "resume_strong.txt")
        weak = match("jd_backend.txt", "resume_weak.txt")
        self.assertLessEqual(weak["coverage"]["required"]["percent"], 20.0)
        self.assertLess(weak["overall"], strong["overall"] - 20)
        gaps = [item.lower() for item in weak["gaps"]["required"]]
        self.assertIn("kubernetes", gaps)
        self.assertIn("redis", gaps)
        self.assertEqual(weak["verdict"], "不建议")

    def test_product_jd_separates_required_and_bonus(self):
        payload = match("jd_product.txt", "resume_weak.txt")
        self.assertGreater(payload["coverage"]["required"]["total"], 0)
        self.assertGreater(payload["coverage"]["bonus"]["total"], 0)
        self.assertEqual(payload["verdict"], "不建议")
        gaps = [item.lower() for item in payload["gaps"]["required"]]
        self.assertIn("sql", gaps)

    def test_positional_arguments_match_flags(self):
        with_flags = match("jd_backend.txt", "resume_strong.txt")
        result = run(
            str(FIXTURES / "jd_backend.txt"),
            str(FIXTURES / "resume_strong.txt"),
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        positional = json.loads(result.stdout)
        self.assertEqual(positional["overall"], with_flags["overall"])
        self.assertEqual(positional["gaps"], with_flags["gaps"])

    def test_stdin_resume(self):
        resume = (FIXTURES / "resume_strong.txt").read_text(encoding="utf-8")
        result = run(
            "--jd",
            str(FIXTURES / "jd_backend.txt"),
            "--resume",
            "-",
            "--json",
            input=resume,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(
            json.loads(result.stdout)["coverage"]["required"]["percent"], 75.0
        )

    def test_single_input_exits_2(self):
        result = run("--jd", str(FIXTURES / "jd_backend.txt"))
        self.assertEqual(result.returncode, 2)

    def test_missing_file_exits_2(self):
        result = run(
            "--jd",
            str(FIXTURES / "missing-jd.txt"),
            "--resume",
            str(FIXTURES / "resume_strong.txt"),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("无法读取", result.stderr)

    def test_help(self):
        result = run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
