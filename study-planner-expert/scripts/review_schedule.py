#!/usr/bin/env python3
"""按 SM-2 变体维护复习卡片，生成未来 N 天的每日复习清单。

records.json 结构：
{
  "version": 1,
  "cards": [
    {"id": "c1", "title": "二分查找边界", "mastery": 0.5,
     "last_review": "2026-10-24", "interval": 1, "ease": 2.5,
     "repetitions": 0, "due": "2026-10-26"}
  ]
}

生成计划：
    python3 review_schedule.py --records records.json --days 7 --today 2026-10-26

更新复习结果（grade 0-5）：
    python3 review_schedule.py --records records.json --advance c1 --grade 4 --today 2026-10-26

SM-2 变体规则：
  - grade >= 3：间隔依次为 1 天、6 天、之后 interval * ease
  - grade < 3：重复次数清零，间隔回到 1 天
  - ease 按标准 SM-2 公式调整，下限 1.3
  - mastery 每次按 (grade - 3) * 0.15 调整，范围 0-1
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_EASE = 2.5
MIN_EASE = 1.3
MAX_MASTERY = 1.0
DEFAULT_MASTERY = 0.5


def clamp(value, low=0.0, high=MAX_MASTERY):
    return max(low, min(high, value))


def parse_date(value, label):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError("%s 不是合法日期（YYYY-MM-DD）：%s" % (label, value))


def normalize_card(item, today):
    if not isinstance(item, dict):
        raise ValueError("cards 中每一项必须是对象")
    card_id = item.get("id")
    if card_id in (None, ""):
        raise ValueError("cards 中存在缺少 id 的卡片")
    card = dict(item)
    card["id"] = str(card_id)
    card["title"] = str(item.get("title") or card["id"])
    try:
        card["mastery"] = clamp(float(item.get("mastery", DEFAULT_MASTERY)))
        card["ease"] = max(MIN_EASE, float(item.get("ease", DEFAULT_EASE)))
        card["interval"] = max(1, int(item.get("interval", 1)))
        card["repetitions"] = max(0, int(item.get("repetitions", 0)))
    except (TypeError, ValueError):
        raise ValueError("卡片 %s 的 mastery/ease/interval/repetitions 不是数字" % card["id"])

    last_review = item.get("last_review")
    card["last_review"] = str(last_review) if last_review else None
    if card["last_review"]:
        parse_date(card["last_review"], "卡片 %s 的 last_review" % card["id"])

    due = item.get("due")
    card["due"] = str(due) if due else (card["last_review"] or today.isoformat())
    parse_date(card["due"], "卡片 %s 的 due" % card["id"])
    return card


def load_records(path, today):
    file_path = Path(path)
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("records 文件不存在：%s" % file_path)
    except json.JSONDecodeError as exc:
        raise ValueError("records 文件不是合法 JSON：%s" % exc)
    if not isinstance(payload, dict) or not isinstance(payload.get("cards"), list):
        raise ValueError("records 文件必须包含 cards 列表")
    cards = [normalize_card(item, today) for item in payload["cards"]]
    return {"version": payload.get("version", 1), "cards": cards}


def save_records(path, payload):
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    Path(path).write_text(text, encoding="utf-8")


def public_card(card):
    return {
        "id": card["id"],
        "title": card["title"],
        "mastery": round(card["mastery"], 2),
        "ease": round(card["ease"], 2),
        "interval": card["interval"],
        "repetitions": card["repetitions"],
        "lastReview": card["last_review"],
        "due": card["due"],
    }


def build_plan(records, today, days):
    buckets = [[] for _ in range(days)]
    for card in records["cards"]:
        due = parse_date(card["due"], "due")
        if due <= today:
            offset = 0
            overdue = (today - due).days
        else:
            offset = (due - today).days
            overdue = 0
        if 0 <= offset < days:
            item = public_card(card)
            item["overdueDays"] = overdue
            buckets[offset].append(item)

    schedule = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        cards = sorted(buckets[offset], key=lambda item: (item["due"], item["id"]))
        schedule.append({"date": day.isoformat(), "count": len(cards), "cards": cards})

    total = sum(len(bucket) for bucket in buckets)
    overdue = sum(
        1
        for bucket in buckets
        for item in bucket
        if item["overdueDays"] > 0
    )
    return {
        "ok": True,
        "today": today.isoformat(),
        "days": days,
        "totalDue": total,
        "overdue": overdue,
        "schedule": schedule,
    }


def advance_card(card, grade, today):
    before = public_card(card)
    interval = card["interval"]
    repetitions = card["repetitions"]
    ease = card["ease"]
    mastery = card["mastery"]

    if grade >= 3:
        if repetitions == 0:
            interval = 1
        elif repetitions == 1:
            interval = 6
        else:
            interval = int(interval * ease + 0.5)
        repetitions += 1
    else:
        repetitions = 0
        interval = 1

    ease = ease + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    ease = max(MIN_EASE, round(ease, 2))
    mastery = round(clamp(mastery + (grade - 3) * 0.15), 2)

    updated = dict(card)
    updated.update(
        {
            "ease": ease,
            "interval": interval,
            "repetitions": repetitions,
            "mastery": mastery,
            "last_review": today.isoformat(),
            "due": (today + timedelta(days=interval)).isoformat(),
        }
    )
    return before, updated


def render_plan(plan):
    lines = ["复习计划（%s 起 %d 天）" % (plan["today"], plan["days"])]
    lines.append("=" * 48)
    for day in plan["schedule"]:
        if not day["cards"]:
            lines.append("%s  0 张" % day["date"])
            continue
        lines.append("%s  %d 张" % (day["date"], day["count"]))
        for card in day["cards"]:
            overdue = (
                "  逾期 %d 天" % card["overdueDays"] if card["overdueDays"] else ""
            )
            lines.append(
                "  - %s %s  due=%s  掌握=%.2f  间隔=%dd%s"
                % (
                    card["id"],
                    card["title"],
                    card["due"],
                    card["mastery"],
                    card["interval"],
                    overdue,
                )
            )
    lines.append("-" * 48)
    lines.append(
        "合计 %d 张（逾期 %d 张）" % (plan["totalDue"], plan["overdue"])
    )
    return "\n".join(lines)


def render_advance(payload):
    before = payload["before"]
    after = payload["after"]
    return "\n".join(
        [
            "%s %s：grade=%d" % (after["id"], after["title"], payload["grade"]),
            "  间隔 %d → %d 天" % (before["interval"], after["interval"]),
            "  易度 %.2f → %.2f" % (before["ease"], after["ease"]),
            "  掌握 %.2f → %.2f" % (before["mastery"], after["mastery"]),
            "  下次复习 %s" % after["due"],
        ]
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="SM-2 变体复习排程：生成未来 N 天计划，或记录一次复习结果。",
        epilog="示例：python3 review_schedule.py --records records.json --days 7 --json",
    )
    parser.add_argument("--records", required=True, help="records.json 路径")
    parser.add_argument("--days", type=int, default=7, help="计划天数（默认 7）")
    parser.add_argument("--today", help="基准日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--advance", metavar="ID", help="更新某张卡片的复习结果")
    parser.add_argument("--grade", type=int, help="复习评分 0-5，与 --advance 一起使用")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    if args.today:
        try:
            today = parse_date(args.today, "--today")
        except ValueError as exc:
            parser.error(str(exc))
    else:
        today = date.today()

    if args.days < 1 and not args.advance:
        parser.error("--days 必须大于 0")

    try:
        records = load_records(args.records, today)
    except ValueError as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 2

    if args.advance:
        if args.grade is None:
            parser.error("--advance 需要同时提供 --grade")
        if not 0 <= args.grade <= 5:
            parser.error("--grade 必须是 0-5 的整数")
        target = None
        for card in records["cards"]:
            if card["id"] == str(args.advance):
                target = card
                break
        if target is None:
            print("错误：找不到卡片 %s" % args.advance, file=sys.stderr)
            return 2

        before, updated = advance_card(target, args.grade, today)
        records["cards"] = [
            updated if card["id"] == updated["id"] else card
            for card in records["cards"]
        ]
        try:
            save_records(args.records, records)
        except OSError as exc:
            print("错误：写入 records 失败：%s" % exc, file=sys.stderr)
            return 2

        payload = {
            "ok": True,
            "grade": args.grade,
            "before": before,
            "after": public_card(updated),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(render_advance(payload))
        return 0

    if args.grade is not None:
        parser.error("--grade 只能与 --advance 一起使用")

    plan = build_plan(records, today, args.days)
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(render_plan(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
