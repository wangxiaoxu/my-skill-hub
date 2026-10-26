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

    def test_numbers_with_units_and_chinese(self):
        self.assertEqual(error_cluster.normalize("timeout=5s"), "timeout=<N>s")
        self.assertEqual(error_cluster.normalize("重试3次"), "重试<N>次")
        self.assertEqual(error_cluster.normalize("took 500ms"), "took <N>ms")

    def test_signed_numbers_normalize_together(self):
        self.assertEqual(
            error_cluster.normalize("offset=-42 +42 42"),
            "offset=<N> <N> <N>",
        )

    def test_identifiers_keep_their_digits(self):
        self.assertNotEqual(
            error_cluster.normalize("abc123 failed"),
            error_cluster.normalize("abc456 failed"),
        )

    def test_http_status_codes_are_preserved(self):
        self.assertEqual(error_cluster.normalize("status=500"), "status=500")
        self.assertEqual(error_cluster.normalize("code: 504"), "code: 504")
        self.assertEqual(
            error_cluster.normalize('HTTP/1.1" 404 12'), 'HTTP/<N>" 404 <N>'
        )


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

    def test_unit_suffixed_numbers_merge(self):
        text = "upstream timeout=5s\nupstream timeout=12s\nupstream timeout=3s\n"
        result = run("-", "--json", input=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["clusterCount"], 1)
        self.assertEqual(payload["clusters"][0]["count"], 3)
        self.assertEqual(
            payload["clusters"][0]["normalized"], "upstream timeout=<N>s"
        )

    def test_chinese_unit_numbers_merge(self):
        text = "连接超时 重试3次\n连接超时 重试5次\n连接超时 重试8次\n"
        result = run("-", "--json", input=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["clusterCount"], 1)
        self.assertEqual(
            payload["clusters"][0]["normalized"], "连接超时 重试<N>次"
        )

    def test_millisecond_values_merge(self):
        text = "took 500ms\ntook 1200ms\n"
        result = run("-", "--json", input=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["clusterCount"], 1)
        self.assertEqual(payload["clusters"][0]["normalized"], "took <N>ms")

    def test_non_status_numbers_use_same_template_family(self):
        # 非状态上下文的数字仍按「同模板即同族」合并。
        text = "retry=500\nretry=404\nattempt=500\n"
        result = run("-", "--json", input=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["clusterCount"], 2)
        counts = sorted(item["count"] for item in payload["clusters"])
        self.assertEqual(counts, [1, 2])
        retry_cluster = [
            item for item in payload["clusters"] if "retry" in item["normalized"]
        ][0]
        self.assertEqual(retry_cluster["normalized"], "retry=<N>")
        self.assertEqual(retry_cluster["count"], 2)

    def test_http_status_codes_stay_distinct(self):
        text = (
            "HTTP status=500 upstream error\n"
            "HTTP status=404 upstream error\n"
        )
        result = run("-", "--json", input=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["clusterCount"], 2)
        self.assertEqual(
            sorted(item["normalized"] for item in payload["clusters"]),
            [
                "HTTP status=404 upstream error",
                "HTTP status=500 upstream error",
            ],
        )

    def test_same_http_status_stays_together(self):
        text = (
            "HTTP status=500 upstream error after 3 retries\n"
            "HTTP status=500 upstream error after 8 retries\n"
        )
        result = run("-", "--json", input=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["clusterCount"], 1)
        self.assertEqual(
            payload["clusters"][0]["normalized"],
            "HTTP status=500 upstream error after <N> retries",
        )

    def test_code_status_codes_stay_distinct(self):
        text = "code=502 upstream error\ncode=504 upstream error\n"
        result = run("-", "--json", input=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["clusterCount"], 2)
        self.assertEqual(
            sorted(item["normalized"] for item in payload["clusters"]),
            ["code=502 upstream error", "code=504 upstream error"],
        )

    def test_status_colon_and_comma_forms_keep_value(self):
        text = (
            "status: 500, upstream error\n"
            "status: 404, upstream error\n"
        )
        result = run("-", "--json", input=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["clusterCount"], 2)

    def test_quoted_json_status_key_keeps_value(self):
        text = (
            '{"status": 500, "message": "upstream error"}\n'
            '{"status": 404, "message": "upstream error"}\n'
        )
        result = run("-", "--json", input=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["clusterCount"], 2)
        self.assertEqual(
            sorted(item["normalized"] for item in payload["clusters"]),
            [
                "status=404 upstream error",
                "status=500 upstream error",
            ],
        )

    def test_access_log_status_codes_stay_distinct(self):
        text = (
            '10.0.0.1 - - "GET /x HTTP/1.1" 404 -\n'
            '10.0.0.1 - - "GET /x HTTP/1.1" 500 -\n'
        )
        result = run("-", "--json", input=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["clusterCount"], 2)
        self.assertEqual(
            sorted(item["normalized"] for item in payload["clusters"]),
            [
                '<IP> - - "GET /x HTTP/<N>" 404 -',
                '<IP> - - "GET /x HTTP/<N>" 500 -',
            ],
        )

    def test_access_log_same_status_merges_across_sizes(self):
        text = (
            '"GET /x HTTP/1.1" 500 1234\n'
            '"GET /x HTTP/1.1" 500 5678\n'
        )
        result = run("-", "--json", input=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["clusterCount"], 1)
        self.assertEqual(
            payload["clusters"][0]["normalized"],
            '"GET /x HTTP/<N>" 500 <N>',
        )

    def test_port_numbers_still_merge(self):
        text = "port=8080\nport=9090\n"
        result = run("-", "--json", input=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["clusterCount"], 1)
        self.assertEqual(payload["clusters"][0]["normalized"], "port=<N>")

    def test_help(self):
        result = run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
