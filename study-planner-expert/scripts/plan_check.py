#!/usr/bin/env python3
"""检查 Markdown 周计划的可执行性。

检查项：
  - 每天任务数是否超过上限（默认 3）
  - 单日总时长是否超过上限（默认 240 分钟）
  - 单个任务块是否超过上限（默认 50 分钟）
  - 任务是否标注时长
  - 日期标题下是否真的解析到了任务
  - 每天是否有验收物（产出/验收/正确率等）
  - 是否有缓冲日
  - 是否有复习安排

用法：
    python3 plan_check.py plan.md
    python3 plan_check.py plan.md --json --max-tasks 3 --max-block-minutes 50
"""

import argparse
import re
import json
import sys

HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
DAY_PREFIX_RE = re.compile(
    r"^(周[一二三四五六日天]|星期[一二三四五六日天]|day\s*\d+|第\s*\d+\s*天|d\s*\d+)",
    re.IGNORECASE,
)
TASK_RE = re.compile(r"^\s*(?:[-*+]\s*(?:\[[ xX]\]\s*)?|\d+[.、)]\s+)(.+)$")
DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(分钟|min|mins|minutes|小时|h|hr)")
ACCEPTANCE_RE = re.compile(
    r"产出|验收|交付物|完成标准|输出[:：]|正确率|测试通过|错题总结|demo",
    re.IGNORECASE,
)
BUFFER_RE = re.compile(r"缓冲|机动|休息|调整|查漏|复盘日")
REVIEW_RE = re.compile(r"复习|回顾|错题|复盘")


def issue(code, severity, day, line, message, suggestion):
    return {
        "code": code,
        "severity": severity,
        "day": day,
        "line": line,
        "message": message,
        "suggestion": suggestion,
    }


def task_duration(line):
    total = 0.0
    found = False
    for value, unit in DURATION_RE.findall(line):
        try:
            minutes = float(value)
        except ValueError:
            continue
        if unit in ("小时", "h", "hr"):
            minutes *= 60
        total += minutes
        found = True
    return total if found else None


def parse_days(text):
    days = []
    current = None
    for number, line in enumerate(text.splitlines(), start=1):
        heading = HEADING_RE.match(line)
        if heading:
            title = heading.group(2)
            if DAY_PREFIX_RE.match(title):
                current = {
                    "name": title,
                    "line": number,
                    "tasks": [],
                    "acceptance": False,
                    "buffer": bool(BUFFER_RE.search(title)),
                }
                days.append(current)
                continue
        if current is None:
            continue
        task = TASK_RE.match(line)
        if task:
            current["tasks"].append((number, line))
        if ACCEPTANCE_RE.search(line):
            current["acceptance"] = True
        if BUFFER_RE.search(line):
            current["buffer"] = True
    return days


def check(text, max_tasks=3, max_daily_minutes=240, max_block_minutes=50):
    days = parse_days(text)
    issues = []

    if not days:
        issues.append(
            issue(
                "no_day_structure",
                "error",
                None,
                0,
                "没有识别到任何「周一 / Day 1 / 第 1 天」形式的日期标题",
                "用二级标题按天组织计划，例如 ## 周一",
            )
        )
        return days, issues

    for day in days:
        name = day["name"]
        tasks = day["tasks"]
        if not tasks:
            issues.append(
                issue(
                    "unparsed_tasks",
                    "warning",
                    name,
                    day["line"],
                    "%s 没有解析到任何任务" % name,
                    "用 `- [ ]` 或 `1.` 列出任务；休息日也写一条「休息/复盘」任务",
                )
            )
        if len(tasks) > max_tasks:
            issues.append(
                issue(
                    "too_many_tasks",
                    "error",
                    name,
                    day["line"],
                    "%s 有 %d 个任务，超过上限 %d" % (name, len(tasks), max_tasks),
                    "合并或删减到 %d 件，优先保留有验收物的任务" % max_tasks,
                )
            )

        minutes = 0.0
        missing = 0
        first_task_line = None
        for number, line in tasks:
            if first_task_line is None:
                first_task_line = number
            duration = task_duration(line)
            if duration is None:
                missing += 1
                continue
            minutes += duration
            if duration > max_block_minutes:
                issues.append(
                    issue(
                        "block_too_long",
                        "warning",
                        name,
                        number,
                        "单个任务块 %.0f 分钟，超过上限 %d" % (duration, max_block_minutes),
                        "拆成两段并中间休息，或把任务拆细",
                    )
                )

        if minutes > max_daily_minutes:
            issues.append(
                issue(
                    "daily_overload",
                    "error",
                    name,
                    day["line"],
                    "%s 总时长 %.0f 分钟，超过上限 %d" % (name, minutes, max_daily_minutes),
                    "减任务或降低单块时长，留出缓冲",
                )
            )

        if missing:
            issues.append(
                issue(
                    "missing_duration",
                    "warning",
                    name,
                    first_task_line or day["line"],
                    "%s 有 %d 个任务没有标注时长" % (name, missing),
                    "写成「做题 30 分钟」「看书 20 页」这类可量化任务",
                )
            )

        if not day["acceptance"] and not day["buffer"]:
            issues.append(
                issue(
                    "missing_acceptance",
                    "warning",
                    name,
                    day["line"],
                    "%s 没有验收物" % name,
                    "加一行「产出：…」或「验收：正确率 ≥80%%」",
                )
            )

    if not any(day["buffer"] for day in days):
        issues.append(
            issue(
                "missing_buffer",
                "warning",
                None,
                0,
                "整份计划没有缓冲日",
                "每周留 1 天缓冲，用来补漏、复盘或休息",
            )
        )

    if not REVIEW_RE.search(text):
        issues.append(
            issue(
                "missing_review",
                "warning",
                None,
                0,
                "整份计划没有复习安排",
                "每天或每周安排固定复习时段，并用间隔重复排程",
            )
        )

    return days, issues


def render(days, issues, path):
    lines = ["计划检查：%s" % path, "=" * 48]
    for day in days:
        lines.append(
            "%-18s 任务 %d 个  验收 %s  缓冲 %s"
            % (
                day["name"],
                len(day["tasks"]),
                "有" if day["acceptance"] else "无",
                "有" if day["buffer"] else "无",
            )
        )
    lines.append("-" * 48)
    if not issues:
        lines.append("未发现问题。")
        return "\n".join(lines)
    lines.append("发现 %d 个问题：" % len(issues))
    for item in issues:
        location = "L%d" % item["line"] if item["line"] else item["day"] or "全篇"
        lines.append("[%s] %s %s：%s" % (item["severity"], location, item["code"], item["message"]))
        lines.append("        建议：%s" % item["suggestion"])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="检查 Markdown 周计划的任务数、时长、验收物、缓冲日与复习安排。",
        epilog="示例：python3 plan_check.py plan.md --json",
    )
    parser.add_argument("plan", help="Markdown 计划文件路径，- 表示 stdin")
    parser.add_argument("--max-tasks", type=int, default=3, help="每日任务数上限（默认 3）")
    parser.add_argument(
        "--max-daily-minutes",
        type=int,
        default=240,
        help="单日总时长上限，分钟（默认 240）",
    )
    parser.add_argument(
        "--max-block-minutes",
        type=int,
        default=50,
        help="单个任务块时长上限，分钟（默认 50）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = parser.parse_args(argv)

    if args.plan == "-":
        text = sys.stdin.read()
    else:
        try:
            with open(args.plan, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as exc:
            print("错误：无法读取计划：%s" % exc, file=sys.stderr)
            return 2

    days, issues = check(
        text,
        max_tasks=args.max_tasks,
        max_daily_minutes=args.max_daily_minutes,
        max_block_minutes=args.max_block_minutes,
    )

    if args.json:
        payload = {
            "ok": not issues,
            "issueCount": len(issues),
            "days": [
                {
                    "name": day["name"],
                    "line": day["line"],
                    "taskCount": len(day["tasks"]),
                    "hasAcceptance": day["acceptance"],
                    "isBuffer": day["buffer"],
                }
                for day in days
            ],
            "issues": issues,
        }
        output = json.dumps(payload, ensure_ascii=False, indent=2)
        print(output)
    else:
        print(render(days, issues, args.plan))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
