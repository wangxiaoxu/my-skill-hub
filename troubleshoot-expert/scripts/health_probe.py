#!/usr/bin/env python3
"""并发探测 URL / host:port，输出状态码、时延与失败分类。

失败分类固定为：dns / tcp / tls / http / timeout / invalid。
HTTP 目标把 2xx、3xx 视为成功；4xx、5xx 归为 http 失败并保留状态码。
TCP 目标（host:port）只验证能否建立连接。

用法：
    python3 health_probe.py https://example.com/health http://127.0.0.1:8000 --json
    python3 health_probe.py db.internal:5432 redis.internal:6379 --timeout 2 --jobs 8
"""

import argparse
import json
import socket
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_RESPONSE_BYTES = 8192


class TargetError(ValueError):
    pass


def parse_target(target):
    """返回 (scheme, host, port, path)；scheme 为 http/https/tcp。"""
    raw = target.strip()
    if not raw:
        raise TargetError("目标为空")
    if "://" in raw:
        parts = urlsplit(raw)
        scheme = parts.scheme.lower()
        if scheme not in DEFAULT_PORTS:
            raise TargetError("不支持的协议：%s" % scheme)
        host = parts.hostname
        if not host:
            raise TargetError("URL 缺少主机名：%s" % raw)
        try:
            port = parts.port or DEFAULT_PORTS[scheme]
        except ValueError as exc:
            raise TargetError("端口不合法：%s" % exc)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        return scheme, host, port, path

    if raw.startswith("["):
        end = raw.find("]")
        if end < 0:
            raise TargetError("IPv6 地址缺少右括号：%s" % raw)
        host = raw[1:end]
        rest = raw[end + 1:]
        if not rest.startswith(":") or not rest[1:].isdigit():
            raise TargetError("TCP 目标必须带端口：%s" % raw)
        return "tcp", host, int(rest[1:]), None

    if raw.count(":") != 1:
        raise TargetError("TCP 目标格式应为 host:port：%s" % raw)
    host, _, port_text = raw.rpartition(":")
    if not host:
        raise TargetError("缺少主机名：%s" % raw)
    if not port_text.isdigit():
        raise TargetError("端口必须是数字：%s" % raw)
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise TargetError("端口超出范围：%s" % raw)
    return "tcp", host, port, None


def _result(target, ok, category, status_code, started, detail):
    return {
        "target": target,
        "ok": ok,
        "category": category,
        "status_code": status_code,
        "latency_ms": round((time.monotonic() - started) * 1000.0, 2),
        "detail": detail,
    }


def _read_http_response(sock):
    chunks = []
    total = 0
    while total < MAX_RESPONSE_BYTES:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    head = b"".join(chunks).split(b"\r\n", 1)[0].decode("latin-1", "replace")
    parts = head.split()
    if len(parts) < 2 or not parts[0].startswith("HTTP/"):
        return None, head or "(空响应)"
    try:
        return int(parts[1]), head
    except ValueError:
        return None, head


def probe(target, timeout=5.0):
    started = time.monotonic()
    try:
        scheme, host, port, path = parse_target(target)
    except TargetError as exc:
        return _result(target, False, "invalid", None, started, str(exc))

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return _result(target, False, "dns", None, started, str(exc))
    except (UnicodeError, OSError) as exc:
        return _result(target, False, "dns", None, started, str(exc))

    family, socktype, proto, _, sockaddr = infos[0]
    sock = socket.socket(family, socktype, proto)
    sock.settimeout(timeout)
    try:
        try:
            sock.connect(sockaddr)
        except socket.timeout:
            return _result(target, False, "timeout", None, started, "TCP 连接超时")
        except ConnectionRefusedError:
            return _result(target, False, "tcp", None, started, "连接被拒绝")
        except OSError as exc:
            return _result(target, False, "tcp", None, started, str(exc))

        if scheme == "https":
            try:
                context = ssl.create_default_context()
                sock = context.wrap_socket(sock, server_hostname=host)
            except socket.timeout:
                return _result(target, False, "timeout", None, started, "TLS 握手超时")
            except ssl.SSLError as exc:
                return _result(target, False, "tls", None, started, str(exc))
            except OSError as exc:
                return _result(target, False, "tls", None, started, str(exc))

        if scheme == "tcp":
            return _result(target, True, "ok", None, started, "TCP 连接成功")

        host_header = host if ":" not in host else "[%s]" % host
        if port not in DEFAULT_PORTS.values():
            host_header += ":%d" % port
        request = (
            "GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: health-probe/1.0\r\n"
            "Accept: */*\r\nConnection: close\r\n\r\n" % (path, host_header)
        )
        try:
            sock.sendall(request.encode("latin-1", "replace"))
            status_code, head = _read_http_response(sock)
        except socket.timeout:
            return _result(target, False, "timeout", None, started, "HTTP 响应超时")
        except OSError as exc:
            return _result(target, False, "http", None, started, str(exc))

        if status_code is None:
            return _result(target, False, "http", None, started, "响应无状态行：%s" % head)
        if 200 <= status_code < 400:
            return _result(target, True, "ok", status_code, started, "HTTP %d" % status_code)
        return _result(
            target, False, "http", status_code, started, "HTTP %d" % status_code
        )
    finally:
        try:
            sock.close()
        except OSError:
            pass


def run(targets, timeout, jobs):
    workers = max(1, min(jobs, len(targets)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(lambda item: probe(item, timeout=timeout), targets))


def render(result):
    if result["ok"]:
        code = "" if result["status_code"] is None else " %d" % result["status_code"]
        return "[OK]   %-45s %8.2fms%s  %s" % (
            result["target"],
            result["latency_ms"],
            code,
            result["detail"],
        )
    return "[FAIL] %-45s %8.2fms  category=%s  %s" % (
        result["target"],
        result["latency_ms"],
        result["category"],
        result["detail"],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="并发探测 URL / host:port，按 DNS/TCP/TLS/HTTP/超时分类失败原因。",
        epilog="示例：python3 health_probe.py https://example.com/health --json",
    )
    parser.add_argument("targets", nargs="+", help="URL 或 host:port")
    parser.add_argument("--timeout", type=float, default=5.0, help="单次探测超时秒数（默认 5）")
    parser.add_argument("--jobs", type=int, default=8, help="并发数（默认 8）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")
    if args.jobs <= 0:
        parser.error("--jobs 必须大于 0")

    results = run(args.targets, timeout=args.timeout, jobs=args.jobs)
    failed = [item for item in results if not item["ok"]]

    if args.json:
        payload = {
            "ok": not failed,
            "total": len(results),
            "failed": len(failed),
            "results": results,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if not failed else 1

    for item in results:
        print(render(item))
    print(
        "共 %d 个目标，成功 %d，失败 %d"
        % (len(results), len(results) - len(failed), len(failed)),
        file=sys.stderr,
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
