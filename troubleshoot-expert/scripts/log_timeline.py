#!/usr/bin/env python3
"""把多个日志文件按时间归一化后合并成一条时间线。

支持的时间戳格式：
  - ISO 8601：2026-10-26T12:34:56.789Z / 2026-10-26 12:34:56,789 +0800
  - nginx：26/Oct/2026:12:34:56 +0800
  - syslog：Oct 26 12:34:56（年份用 --year 或从同文件其他时间戳推断）

时区处理：带时区的时间戳统一转成 UTC；不带时区的按 UTC 处理。
无时间戳的行会挂在同文件上一条有时间戳的记录上，并标记为 untimed。

用法：
    python3 log_timeline.py a.log b.log --since 2026-10-26T10:00:00 --json
    python3 log_timeline.py app.log --grep "timeout" --ignore-case
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ISO_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:?\d{2})?)"
)
NGINX_RE = re.compile(
    r"(?P<ts>\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}(?:\s+[+-]\d{4})?)"
)
SYSLOG_RE = re.compile(r"(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})")
DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _to_naive_utc(value):
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def parse_timestamp(text, default_year=None):
    """从一段文本中提取并解析时间戳，失败返回 None。"""
    match = ISO_RE.search(text)
    if match:
        raw = match.group("ts").replace(",", ".")
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        raw = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", raw)
        try:
            return _to_naive_utc(datetime.fromisoformat(raw))
        except ValueError:
            return None

    match = NGINX_RE.search(text)
    if match:
        raw = match.group("ts")
        for fmt in ("%d/%b/%Y:%H:%M:%S %z", "%d/%b/%Y:%H:%M:%S"):
            try:
                return _to_naive_utc(datetime.strptime(raw, fmt))
            except ValueError:
                continue
        return None

    match = SYSLOG_RE.search(text)
    if match:
        year = default_year or datetime.now().year
        raw = re.sub(r"\s+", " ", match.group("ts"))
        try:
            return datetime.strptime("%d %s" % (year, raw), "%Y %b %d %H:%M:%S")
        except ValueError:
            return None

    return None


def parse_bound(value):
    """解析 --since / --until 的取值。"""
    if DATE_ONLY_RE.match(value):
        return datetime.strptime(value, "%Y-%m-%d")
    parsed = parse_timestamp(value)
    if parsed is None:
        raise argparse.ArgumentTypeError("无法解析时间：%s" % value)
    return parsed


def parse_until(value):
    """解析 --until；只给日期时取当天 23:59:59.999999。"""
    if DATE_ONLY_RE.match(value):
        return datetime.strptime(value, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
    return parse_bound(value)


def format_timestamp(value):
    if value is None:
        return None
    text = value.strftime("%Y-%m-%dT%H:%M:%S")
    if value.microsecond:
        text += ".%06d" % value.microsecond
    return text + "Z"


def read_lines(path):
    with open(str(path), "r", encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\r\n") for line in handle]


def infer_year(lines):
    for line in lines:
        parsed = parse_timestamp(line[:120])
        if parsed is not None:
            return parsed.year
    return None


def build_records(paths, default_year=None):
    """把多个文件展开成记录列表；无时间戳行标注 untimed。"""
    records = []
    for path in paths:
        lines = read_lines(path)
        file_year = default_year or infer_year(lines) or datetime.now().year
        parsed = [
            (number, parse_timestamp(line[:120], file_year))
            for number, line in enumerate(lines, start=1)
        ]
        first_ts = next((ts for _, ts in parsed if ts is not None), None)
        last_ts = None

        for (number, timestamp), line in zip(parsed, lines):
            untimed = timestamp is None
            anchor = last_ts if untimed else None
            if untimed:
                anchor = anchor or first_ts
            else:
                last_ts = timestamp
            records.append(
                {
                    "timestamp": anchor if untimed else timestamp,
                    "source": path.name,
                    "path": str(path),
                    "line": number,
                    "untimed": untimed,
                    "text": line,
                }
            )

    records.sort(
        key=lambda item: (
            item["timestamp"] is None,
            item["timestamp"] or datetime.max,
            item["source"],
            item["line"],
        )
    )
    return records


def filter_records(records, since=None, until=None, pattern=None, ignore_case=False):
    """按时间窗口与正则过滤；untimed 行用其锚点时间参与过滤。"""
    regex = None
    if pattern is not None:
        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(pattern, flags)

    output = []
    for record in records:
        timestamp = record["timestamp"]
        if since is not None and (timestamp is None or timestamp < since):
            continue
        if until is not None and (timestamp is None or timestamp > until):
            continue
        if regex is not None and not regex.search(record["text"]):
            continue
        output.append(record)
    return output


def render(record):
    stamp = format_timestamp(record["timestamp"]) or "-"
    marker = "[untimed] " if record["untimed"] else ""
    return "%s  %s:%d  %s%s" % (
        stamp.ljust(27),
        record["source"],
        record["line"],
        marker,
        record["text"],
    )


def to_json_record(record):
    return {
        "timestamp": format_timestamp(record["timestamp"]),
        "source": record["source"],
        "path": record["path"],
        "line": record["line"],
        "untimed": record["untimed"],
        "text": record["text"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="合并多个日志文件，按归一化时间戳排序输出时间线。",
        epilog="示例：python3 log_timeline.py a.log b.log --since 2026-10-26 --json",
    )
    parser.add_argument("files", nargs="+", help="日志文件路径（可多个）")
    parser.add_argument("--since", type=parse_bound, help="起始时间（含），支持 ISO 或 YYYY-MM-DD")
    parser.add_argument("--until", type=parse_until, help="结束时间（含），支持 ISO 或 YYYY-MM-DD")
    parser.add_argument("--grep", help="只保留匹配该正则的整行（Python 正则语法）")
    parser.add_argument("--ignore-case", action="store_true", help="--grep 忽略大小写")
    parser.add_argument("--year", type=int, help="syslog 时间戳缺失年份时使用该年份")
    parser.add_argument("--limit", type=int, default=0, help="最多输出 N 行，0 表示不限制")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    if args.grep is not None:
        try:
            re.compile(args.grep)
        except re.error as exc:
            parser.error("--grep 不是合法正则：%s" % exc)

    paths = [Path(item) for item in args.files]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        print("错误：文件不存在：%s" % ", ".join(missing), file=sys.stderr)
        return 2

    records = build_records(paths, default_year=args.year)
    filtered = filter_records(
        records,
        since=args.since,
        until=args.until,
        pattern=args.grep,
        ignore_case=args.ignore_case,
    )
    returned = filtered[: args.limit] if args.limit and args.limit > 0 else filtered

    if args.json:
        payload = {
            "files": [str(path) for path in paths],
            "total": len(records),
            "returned": len(returned),
            "untimed": sum(1 for item in returned if item["untimed"]),
            "since": format_timestamp(args.since),
            "until": format_timestamp(args.until),
            "records": [to_json_record(item) for item in returned],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for record in returned:
        print(render(record))
    print(
        "共 %d 条，输出 %d 条（其中 untimed %d 条）"
        % (len(records), len(returned), sum(1 for item in returned if item["untimed"])),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
