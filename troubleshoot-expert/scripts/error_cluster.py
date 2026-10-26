#!/usr/bin/env python3
"""把日志里的错误消息归一化后聚类，输出 Top N 错误族。

归一化会替换：时间戳、UUID、十六进制、IP、文件路径、数字
（包括紧跟单位或中文的数字，如 5s、500ms、重试3次）。
例外：status=/code=/access log 状态位中的 3 位 HTTP 状态码保留原值，
避免把 500 与 404 合并成同一错误族。
JSON 日志会优先取 message / msg / error 字段，其余按整行处理。

用法：
    python3 error_cluster.py app.log --top 5
    cat app.log | python3 error_cluster.py - --json
    python3 error_cluster.py app.log --field err --top 20
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone

ISO_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:?\d{2})?"
)
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
HEX_RE = re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-fA-F]{12,}\b")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")
PATH_RE = re.compile(r"(?:[A-Za-z]:\\[^\s\"']+|/[\w.\-]+(?:/[\w.\-]+)+)")
# 数字可能紧跟单位（5s、500ms）或中文（重试3次），因此不能用尾部 \b；
# 只要求左侧不是字母/数字，避免把 abc123 这类标识符拆开。
NUM_RE = re.compile(r"(?<![A-Za-z0-9])[+-]?\d+(?:\.\d+)?")
WS_RE = re.compile(r"\s+")

# HTTP 状态码只在明确的状态上下文里保留：status=/code=/access log 状态位。
# 保护发生在所有数字替换之前，归一化结束后再原样恢复。
STATUS_PATTERNS = (
    re.compile(r"(?i)([\"']?\bstatus[\"']?\s*[=:]\s*)(\d{3})(?=\b|[,.;]|$)"),
    re.compile(r"(?i)([\"']?\bcode[\"']?\s*[=:]\s*)(\d{3})(?=\b|[,.;]|$)"),
    re.compile(r'(HTTP/\d(?:\.\d)?"\s+)(\d{3})(?=\s|$)'),
)

JSON_MESSAGE_KEYS = ("message", "msg", "error", "err", "log", "detail")
JSON_TIME_KEYS = ("timestamp", "time", "ts", "@timestamp", "date")
JSON_LEVEL_KEYS = ("level", "severity", "lvl")
JSON_STATUS_KEYS = ("status", "status_code", "statusCode", "http_status", "code")


# 占位符里的 index 必须紧贴字母（STATUS0），让 NUM_RE 的
# (?<![A-Za-z0-9]) lookbehind 不命中；若改成 "STATUS-0" 或纯数字，
# 状态码会在 NUM_RE 阶段被替换成 <N>，保护失效。
def _status_placeholder(index):
    return "\x01STATUS%d\x02" % index


def _protect_status_codes(text):
    """把状态码替换成不含数字的占位符，返回 (文本, 原值列表)。"""
    values = []

    def replace(match):
        values.append(match.group(2))
        return match.group(1) + _status_placeholder(len(values) - 1)

    for pattern in STATUS_PATTERNS:
        text = pattern.sub(replace, text)
    return text, values


def normalize(message):
    """把一条错误消息归一化成语义骨架；HTTP 状态码保留原值。"""
    text, status_codes = _protect_status_codes(message)
    text = ISO_TS_RE.sub("<TS>", text)
    text = UUID_RE.sub("<UUID>", text)
    text = HEX_RE.sub("<HEX>", text)
    text = IP_RE.sub("<IP>", text)
    text = PATH_RE.sub("<PATH>", text)
    text = DATE_RE.sub("<DATE>", text)
    text = NUM_RE.sub("<N>", text)
    text = WS_RE.sub(" ", text).strip()
    for index, value in enumerate(status_codes):
        text = text.replace(_status_placeholder(index), value)
    return text


def parse_timestamp(text):
    match = ISO_TS_RE.search(text)
    if not match:
        return None
    raw = match.group(0).replace(",", ".")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    raw = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", raw)
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _json_status(payload):
    """从结构化日志的 status/code 字段取 3 位状态码。"""
    for key in JSON_STATUS_KEYS:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and 100 <= value <= 599:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            number = int(value.strip())
            if 100 <= number <= 599:
                return number
    return None


def _strip_leading_timestamp(text):
    """去掉行首时间戳，让纯文本行与结构化日志的消息体可比。"""
    text = text.strip()
    match = ISO_TS_RE.match(text)
    if match:
        rest = text[match.end():].lstrip(" \t[]()|-:")
        if rest:
            return rest
    elif text.startswith("["):
        end = text.find("]")
        if 0 < end <= 40 and parse_timestamp(text[1:end]) is not None:
            rest = text[end + 1:].lstrip(" \t-|:")
            if rest:
                return rest
    return text


def extract(line, field=None):
    """返回 (消息文本, 时间戳或 None)。"""
    stripped = line.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            keys = (field,) if field else JSON_MESSAGE_KEYS
            message = None
            for key in keys:
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    message = value.strip()
                    break
            timestamp = None
            for key in JSON_TIME_KEYS:
                value = payload.get(key)
                if isinstance(value, str):
                    timestamp = parse_timestamp(value)
                    if timestamp is not None:
                        break
            if message is not None:
                status = _json_status(payload)
                if status is not None and "status=" not in message.lower():
                    message = "status=%d %s" % (status, message)
                level = None
                for key in JSON_LEVEL_KEYS:
                    value = payload.get(key)
                    if isinstance(value, str) and value.strip():
                        level = value.strip().upper()
                        break
                if level and not message.upper().startswith(level):
                    message = level + " " + message
                return message, timestamp
    return _strip_leading_timestamp(stripped), parse_timestamp(stripped)


def read_lines(path):
    if path == "-":
        return [line.rstrip("\r\n") for line in sys.stdin]
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\r\n") for line in handle]


def cluster_lines(lines, field=None, top=10):
    clusters = {}
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        message, timestamp = extract(line, field=field)
        key = normalize(message)
        entry = clusters.get(key)
        if entry is None:
            entry = {
                "normalized": key,
                "count": 0,
                "first_seen": None,
                "last_seen": None,
                "sample": message,
                "sample_line": number,
                "lines": [],
            }
            clusters[key] = entry
        entry["count"] += 1
        entry["lines"].append(number)
        if timestamp is not None:
            stamp = timestamp.isoformat()
            if entry["first_seen"] is None or stamp < entry["first_seen"]:
                entry["first_seen"] = stamp
            if entry["last_seen"] is None or stamp > entry["last_seen"]:
                entry["last_seen"] = stamp

    ordered = sorted(
        clusters.values(),
        key=lambda item: (-item["count"], item["first_seen"] or "~", item["normalized"]),
    )
    if top and top > 0:
        ordered = ordered[:top]
    return ordered


def render(entry):
    return (
        "%4d 次  首次=%s  末次=%s\n"
        "      归一化: %s\n"
        "      样例(第 %d 行): %s"
        % (
            entry["count"],
            entry["first_seen"] or "-",
            entry["last_seen"] or "-",
            entry["normalized"],
            entry["sample_line"],
            entry["sample"],
        )
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "错误消息归一化并聚类，输出 Top N 错误族；"
            "status=/code=/access log 状态位中的 HTTP 状态码保留原值。"
        ),
        epilog="示例：python3 error_cluster.py app.log --top 5 --json",
    )
    parser.add_argument("files", nargs="*", default=["-"], help="日志文件，默认从 stdin 读取（- 表示 stdin）")
    parser.add_argument("--top", type=int, default=10, help="输出前 N 个错误族，0 表示全部（默认 10）")
    parser.add_argument("--field", help="JSON 日志中作为消息的字段名（默认自动尝试 message/msg/error 等）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    paths = args.files or ["-"]
    lines = []
    for path in paths:
        try:
            lines.extend(read_lines(path))
        except OSError as exc:
            print("错误：无法读取 %s：%s" % (path, exc), file=sys.stderr)
            return 2

    clusters = cluster_lines(lines, field=args.field, top=args.top)

    if args.json:
        payload = {
            "totalLines": len(lines),
            "clusterCount": len(clusters),
            "top": args.top,
            "clusters": clusters,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not clusters:
        print("没有可聚类的非空行。")
        return 0

    print("Top %d 错误族（共扫描 %d 行）" % (len(clusters), len(lines)))
    print("=" * 60)
    for index, entry in enumerate(clusters, start=1):
        print("#%d %s" % (index, render(entry)))
        print("-" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
