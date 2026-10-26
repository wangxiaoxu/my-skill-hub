#!/usr/bin/env python3
"""简历体检：找出联系人缺失、无量化、弱动词、超长段落、日期格式不一致等硬伤。

用法：
    python3 resume_lint.py resume.txt
    python3 resume_lint.py resume.txt --json
    cat resume.txt | python3 resume_lint.py - --json

退出码：0 表示未发现问题，1 表示发现问题，2 表示输入错误。
"""

import argparse
import json
import re
import sys

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?86[\s-]?)?1[3-9]\d{9}")
METRIC_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％|万|亿|倍|人|次|个|条|天|小时|分钟|ms|QPS|单|元|美元)"
)
PLACEHOLDER_RE = re.compile(r"<待补[：:][^>]*>")
BULLET_RE = re.compile(r"^\s*(?:[-*•·●▪◦]|\d+[.、)）])\s*")

WEAK_VERBS = {
    "负责": "主导 / 牵头 / 交付",
    "参与": "主导 / 推动 / 设计",
    "协助": "协同 / 支撑 / 推动",
    "帮助": "支持 / 赋能 / 带动",
    "配合": "协同 / 推动 / 对接",
    "完成": "交付 / 落地 / 上线",
    "进行": "开展 / 实施 / 推进",
    "跟进": "推动 / 闭环 / 复盘",
    "整理": "沉淀 / 输出 / 搭建",
    "支持": "支撑 / 驱动 / 保障",
    "做了": "交付 / 搭建 / 优化",
    "帮忙": "支持 / 推动",
    "responsible for": "led / owned / delivered",
    "helped": "enabled / drove",
    "assisted": "supported / coordinated",
    "participated in": "led / drove / designed",
    "worked on": "delivered / built",
}

DATE_PATTERNS = [
    ("中文年月", re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月")),
    ("斜杠", re.compile(r"\b\d{4}/\d{1,2}\b")),
    ("点号", re.compile(r"\b\d{4}\.\d{1,2}\b")),
    ("短横线", re.compile(r"\b\d{4}-\d{1,2}\b")),
    (
        "英文月份",
        re.compile(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b",
            re.IGNORECASE,
        ),
    ),
]

LONG_LINE_THRESHOLD = 120


def issue(code, severity, line, message, suggestion):
    return {
        "code": code,
        "severity": severity,
        "line": line,
        "message": message,
        "suggestion": suggestion,
    }


def strip_bullet(line):
    return BULLET_RE.sub("", line).strip()


def lint(text):
    lines = text.splitlines()
    issues = []

    if not EMAIL_RE.search(text) and not PHONE_RE.search(text):
        issues.append(
            issue(
                "missing_contact",
                "error",
                0,
                "未找到邮箱或手机号",
                "在页首补上邮箱与手机号，格式如 name@example.com / 13800000000",
            )
        )

    metrics = METRIC_RE.findall(text)
    if not metrics:
        issues.append(
            issue(
                "no_metrics",
                "error",
                0,
                "全篇没有可识别的量化结果",
                "至少补 3 处数字：规模、效率、占比、周期；没有数据就写 <待补：指标>",
            )
        )
    elif len(metrics) < 2:
        issues.append(
            issue(
                "insufficient_metrics",
                "warning",
                0,
                "量化表达只有 %d 处" % len(metrics),
                "再补 1–2 处可验证的数字或范围",
            )
        )

    for number, line in enumerate(lines, start=1):
        if PLACEHOLDER_RE.search(line):
            issues.append(
                issue(
                    "placeholder",
                    "warning",
                    number,
                    "存在待补占位符",
                    "补齐真实数据；拿不到就删除该指标并改用可验证的范围",
                )
            )

        stripped = strip_bullet(line)
        low = stripped.lower()
        for verb, suggestion in WEAK_VERBS.items():
            if low.startswith(verb):
                issues.append(
                    issue(
                        "weak_verb",
                        "warning",
                        number,
                        "弱动词开头：「%s」" % verb,
                        "改为 %s，并在后面写清对象与结果" % suggestion,
                    )
                )
                break

        if len(line) > LONG_LINE_THRESHOLD and not line.lstrip().startswith("#"):
            issues.append(
                issue(
                    "long_paragraph",
                    "warning",
                    number,
                    "单行 %d 字符，超过 %d" % (len(line), LONG_LINE_THRESHOLD),
                    "拆成 2–4 条 bullet，每条只讲一个动作与一个结果",
                )
            )

    styles = [name for name, pattern in DATE_PATTERNS if pattern.search(text)]
    if len(styles) > 1:
        issues.append(
            issue(
                "date_format_inconsistent",
                "warning",
                0,
                "时间格式混用：%s" % "、".join(styles),
                "全篇统一为一种格式，推荐 YYYY.MM 或 YYYY-MM",
            )
        )

    return issues


def render(issues):
    if not issues:
        return "简历体检通过：未发现硬伤。"
    lines = ["简历体检：%d 个问题" % len(issues), "=" * 48]
    for item in issues:
        location = "L%d" % item["line"] if item["line"] else "全篇"
        lines.append(
            "[%s] %s %s：%s\n        建议：%s"
            % (item["severity"], location, item["code"], item["message"], item["suggestion"])
        )
    return "\n".join(lines)


def read_source(source):
    if source == "-":
        return sys.stdin.read()
    with open(source, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="检查简历的常见硬伤并给出逐行建议。",
        epilog="示例：python3 resume_lint.py resume.txt --json",
    )
    parser.add_argument("resume", help="简历文件路径，- 表示 stdin")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    try:
        text = read_source(args.resume)
    except OSError as exc:
        print("错误：无法读取简历：%s" % exc, file=sys.stderr)
        return 2

    issues = lint(text)
    if args.json:
        print(
            json.dumps(
                {"ok": not issues, "issueCount": len(issues), "issues": issues},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render(issues))
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
