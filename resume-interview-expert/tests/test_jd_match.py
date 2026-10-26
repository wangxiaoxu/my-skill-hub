import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "jd_match.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(ROOT / "scripts"))
import jd_match  # noqa: E402


def invoke(*args, **kwargs):
    command = [sys.executable, str(SCRIPT), *args]
    return subprocess.run(
        command, capture_output=True, text=True, **kwargs
    )


def match(jd, resume):
    result = invoke("--jd", str(FIXTURES / jd), "--resume", str(FIXTURES / resume), "--json")
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class SynonymBoundaryTest(unittest.TestCase):
    def test_javascript_does_not_match_node_or_vue_js(self):
        self.assertFalse(jd_match.contains("JavaScript", "熟悉 Node.js 后端开发"))
        self.assertFalse(jd_match.contains("JavaScript", "用 Vue.js 做过后台"))

    def test_javascript_matches_javascript_itself(self):
        self.assertTrue(jd_match.contains("JavaScript", "熟悉 JavaScript"))
        self.assertTrue(jd_match.contains("JavaScript", "熟悉 JS"))

    def test_go_short_token_is_case_sensitive(self):
        self.assertFalse(jd_match.contains("Go", "go to market"))
        self.assertTrue(jd_match.contains("Go", "Go"))
        self.assertTrue(jd_match.contains("Go", "Golang"))


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
        result = invoke(
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
        result = invoke(
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

    def test_synonyms_are_normalized(self):
        with tempfile.TemporaryDirectory() as tempdir:
            jd_path = Path(tempdir) / "jd.txt"
            resume_path = Path(tempdir) / "resume.txt"
            jd_path.write_text(
                "任职要求：\n"
                "1. 熟悉 K8s\n"
                "2. 熟悉 JS\n"
                "3. 熟悉 Postgres\n"
                "4. 熟悉 Golang\n",
                encoding="utf-8",
            )
            resume_path.write_text(
                "使用 Kubernetes 部署，使用 JavaScript 开发，"
                "使用 PostgreSQL 存储，使用 Go 写服务。\n",
                encoding="utf-8",
            )
            result = invoke(
                "--jd",
                str(jd_path),
                "--resume",
                str(resume_path),
                "--json",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        matched = {
            item["keyword"]
            for item in payload["keywords"]["required"]
            if item["matched"]
        }
        self.assertEqual(
            matched, {"Kubernetes", "JavaScript", "PostgreSQL", "Go"}
        )
        self.assertEqual(payload["coverage"]["required"]["percent"], 100.0)
        self.assertEqual(payload["gaps"]["required"], [])

    def test_go_does_not_match_google(self):
        with tempfile.TemporaryDirectory() as tempdir:
            jd_path = Path(tempdir) / "jd.txt"
            resume_path = Path(tempdir) / "resume.txt"
            jd_path.write_text("任职要求：熟悉 Google Cloud。\n", encoding="utf-8")
            resume_path.write_text("使用 Go 写服务。\n", encoding="utf-8")
            result = invoke(
                "--jd",
                str(jd_path),
                "--resume",
                str(resume_path),
                "--json",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        keywords = [
            item["keyword"]
            for item in payload["keywords"]["required"]
            + payload["keywords"]["bonus"]
        ]
        self.assertNotIn("Go", keywords)

    def test_empty_jd_exits_2(self):
        with tempfile.TemporaryDirectory() as tempdir:
            jd_path = Path(tempdir) / "jd.txt"
            resume_path = Path(tempdir) / "resume.txt"
            jd_path.write_text("", encoding="utf-8")
            resume_path.write_text("张三\n电话：13800000000\n", encoding="utf-8")
            result = invoke(
                "--jd",
                str(jd_path),
                "--resume",
                str(resume_path),
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("JD 为空", result.stderr)

    def test_keywordless_jd_exits_2(self):
        with tempfile.TemporaryDirectory() as tempdir:
            jd_path = Path(tempdir) / "jd.txt"
            resume_path = Path(tempdir) / "resume.txt"
            jd_path.write_text("你好，欢迎加入我们。\n", encoding="utf-8")
            resume_path.write_text("张三\n电话：13800000000\n", encoding="utf-8")
            result = invoke(
                "--jd",
                str(jd_path),
                "--resume",
                str(resume_path),
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("未能从 JD 中识别出任何关键词", result.stderr)

    def test_boilerplate_tokens_are_not_keywords(self):
        with tempfile.TemporaryDirectory() as tempdir:
            jd_path = Path(tempdir) / "jd.txt"
            resume_path = Path(tempdir) / "resume.txt"
            jd_path.write_text(
                "公司介绍：\n"
                "We offer a competitive compensation package, a friendly office "
                "in Shanghai, remote-friendly culture, and a full-stack hiring stack.\n\n"
                "任职要求：\n"
                "熟悉 Redis。\n",
                encoding="utf-8",
            )
            resume_path.write_text("张三\n熟悉 Redis\n", encoding="utf-8")
            result = invoke(
                "--jd",
                str(jd_path),
                "--resume",
                str(resume_path),
                "--json",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        items = payload["keywords"]["required"] + payload["keywords"]["bonus"]
        keywords = {item["keyword"].lower() for item in items}
        self.assertEqual(keywords, {"redis"})
        redis_item = [
            item for item in items if item["keyword"].lower() == "redis"
        ][0]
        self.assertTrue(redis_item["matched"])

    def test_dotted_and_hyphenated_tokens_stay_whole(self):
        with tempfile.TemporaryDirectory() as tempdir:
            jd_path = Path(tempdir) / "jd.txt"
            resume_path = Path(tempdir) / "resume.txt"
            jd_path.write_text(
                "任职要求：\n"
                "1. 熟悉 Node.js\n"
                "2. 有 full-stack 经验\n",
                encoding="utf-8",
            )
            resume_path.write_text(
                "使用 Node.js 与 fullstack 开发\n", encoding="utf-8"
            )
            result = invoke(
                "--jd",
                str(jd_path),
                "--resume",
                str(resume_path),
                "--json",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        items = payload["keywords"]["required"] + payload["keywords"]["bonus"]
        keywords = {item["keyword"] for item in items}
        self.assertEqual(keywords, {"Node.js", "full-stack"})
        matched = {item["keyword"] for item in items if item["matched"]}
        self.assertIn("Node.js", matched)
        self.assertIn("full-stack", matched)

    def test_lowercase_external_token_in_requirement_section_is_kept(self):
        with tempfile.TemporaryDirectory() as tempdir:
            jd_path = Path(tempdir) / "jd.txt"
            resume_path = Path(tempdir) / "resume.txt"
            jd_path.write_text("任职要求：\n熟悉 terraform\n", encoding="utf-8")
            resume_path.write_text(
                "使用 terraform 管理基础设施\n", encoding="utf-8"
            )
            result = invoke(
                "--jd",
                str(jd_path),
                "--resume",
                str(resume_path),
                "--json",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        items = payload["keywords"]["required"] + payload["keywords"]["bonus"]
        keywords = {item["keyword"].lower() for item in items}
        self.assertIn("terraform", keywords)

    def test_single_input_exits_2(self):
        result = invoke("--jd", str(FIXTURES / "jd_backend.txt"))
        self.assertEqual(result.returncode, 2)

    def test_missing_file_exits_2(self):
        result = invoke(
            "--jd",
            str(FIXTURES / "missing-jd.txt"),
            "--resume",
            str(FIXTURES / "resume_strong.txt"),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("无法读取", result.stderr)

    def test_help(self):
        result = invoke("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
