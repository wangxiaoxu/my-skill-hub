import json
import socket
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "health_probe.py"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == "/slow":
                time.sleep(1.0)
                body = b"slow"
                self.send_response(200)
            elif self.path == "/missing":
                body = b"missing"
                self.send_response(404)
            else:
                body = b"ok"
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        pass


def run(*args, **kwargs):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        capture_output=True,
        text=True,
        **kwargs
    )


def free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class HealthProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_http_200(self):
        result = run("http://127.0.0.1:%d/ok" % self.port, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["results"][0]["status_code"], 200)
        self.assertEqual(payload["results"][0]["category"], "ok")

    def test_http_404_is_failure_with_status_code(self):
        result = run("http://127.0.0.1:%d/missing" % self.port, "--json")
        self.assertEqual(result.returncode, 1)
        item = json.loads(result.stdout)["results"][0]
        self.assertFalse(item["ok"])
        self.assertEqual(item["category"], "http")
        self.assertEqual(item["status_code"], 404)

    def test_connection_refused(self):
        port = free_port()
        result = run("http://127.0.0.1:%d/" % port, "--timeout", "1", "--json")
        self.assertEqual(result.returncode, 1)
        item = json.loads(result.stdout)["results"][0]
        self.assertEqual(item["category"], "tcp")
        self.assertIn("拒绝", item["detail"])

    def test_dns_failure(self):
        result = run("http://bad host:80/", "--json")
        self.assertEqual(result.returncode, 1)
        item = json.loads(result.stdout)["results"][0]
        self.assertEqual(item["category"], "dns")

    def test_http_timeout(self):
        result = run(
            "http://127.0.0.1:%d/slow" % self.port,
            "--timeout",
            "0.2",
            "--json",
        )
        self.assertEqual(result.returncode, 1)
        item = json.loads(result.stdout)["results"][0]
        self.assertEqual(item["category"], "timeout")

    def test_tcp_target(self):
        result = run("127.0.0.1:%d" % self.port, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)["results"][0]
        self.assertTrue(item["ok"])
        self.assertIsNone(item["status_code"])

    def test_invalid_target(self):
        result = run("127.0.0.1", "--json")
        self.assertEqual(result.returncode, 1)
        item = json.loads(result.stdout)["results"][0]
        self.assertEqual(item["category"], "invalid")

    def test_multiple_targets(self):
        result = run(
            "http://127.0.0.1:%d/ok" % self.port,
            "127.0.0.1:%d" % self.port,
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["failed"], 0)

    def test_help(self):
        result = run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
