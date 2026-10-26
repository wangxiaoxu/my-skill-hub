#!/usr/bin/env python3
"""对齐 JD 与简历：抽取关键词、区分必须/加分、计算三维分数与缺口。

输入：JD 文本 + 简历文本（文件或 - 表示 stdin）。
输出：覆盖/深度/证据三个维度分数、综合分、结论、缺口清单。
JD 为空或抽不出任何关键词时退出码 2，不给出误导性结论。
词典外的英文 token 只在含大写/数字/点，或出现在任职要求类章节时计入，
避免公司介绍等套话进入 required。

用法：
    python3 jd_match.py --jd jd.txt --resume resume.txt
    python3 jd_match.py jd.txt resume.txt --json
    cat resume.txt | python3 jd_match.py --jd jd.txt --resume - --json
"""

import argparse
import json
import sys
import re

# 常见技能/能力词典。英文词条用小写做键，中文按子串匹配。
TERMS = [
    # 后端
    "Java", "Python", "Go", "C++", "Spring", "Spring Boot", "Spring Cloud",
    "MySQL", "PostgreSQL", "Redis", "Kafka", "RabbitMQ", "Docker", "Kubernetes",
    "Linux", "Git", "微服务", "分布式", "高并发", "大流量", "消息队列", "中间件",
    "缓存", "数据库", "架构设计", "系统设计", "接口设计", "性能优化", "单元测试",
    "容器化", "排障", "线上故障", "容量规划", "稳定性",
    # 前端
    "JavaScript", "TypeScript", "React", "Vue", "Node.js", "Webpack",
    "前端工程化", "响应式", "小程序", "跨端", "组件化", "可视化",
    # 数据
    "SQL", "数据分析", "数据挖掘", "机器学习", "深度学习", "推荐系统",
    "数据仓库", "指标体系", "A/B 测试", "用户画像", "数据可视化", "Hive",
    "Spark", "Flink", "Tableau",
    # 产品
    "需求分析", "产品设计", "用户研究", "竞品分析", "原型设计", "项目管理",
    "跨部门协作", "用户增长", "商业模式", "埋点", "转化率", "留存率",
    "敏捷开发", "PRD", "开源项目",
    # 运营
    "用户运营", "内容运营", "活动策划", "社群运营", "增长黑客", "投放",
    "拉新", "促活", "留存", "转化", "复购", "商业化",
    # 销售
    "客户开发", "商务谈判", "渠道管理", "大客户", "销售管理", "业绩目标",
    # 通用
    "沟通能力", "团队管理", "英语", "抗压能力", "学习能力", "责任心",
    "执行力", "汇报", "文档",
]

# 同义词组：canonical_lower -> (统一展示名, 所有可匹配写法)
# 关键词按 canonical 去重，匹配与计数时遍历所有写法。
SYNONYM_GROUPS = {
    "kubernetes": ("Kubernetes", ["kubernetes", "k8s"]),
    "javascript": ("JavaScript", ["javascript", "js"]),
    "postgresql": ("PostgreSQL", ["postgresql", "postgres", "pg"]),
    "go": ("Go", ["go", "golang"]),
    "full-stack": ("full-stack", ["full-stack", "fullstack"]),
}
VARIANT_TO_CANONICAL = {}
for _canonical, (_display, _variants) in SYNONYM_GROUPS.items():
    for _variant in _variants:
        VARIANT_TO_CANONICAL[_variant] = _canonical

# ≤2 字符的短 token 容易误命中普通英文，改为按大小写精确匹配。
SHORT_TOKEN_CASE_FORMS = {
    "go": ("Go",),
    "js": ("JS", "js"),
    "pg": ("PG", "pg"),
}


def canonical_key(term):
    low = term.lower()
    return VARIANT_TO_CANONICAL.get(low, low)


def keyword_variants(term):
    canonical = canonical_key(term)
    if canonical in SYNONYM_GROUPS:
        return SYNONYM_GROUPS[canonical][1]
    return [term]


def _token_pattern(token, ignore_case):
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(
        r"(?<![A-Za-z0-9.])%s(?![A-Za-z0-9])" % re.escape(token), flags
    )


def _variant_forms(variant):
    if len(variant) <= 2:
        return SHORT_TOKEN_CASE_FORMS.get(variant, (variant,)), False
    return (variant,), True


EN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#._-]*")
METRIC_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％|万|亿|倍|人|次|个|条|天|小时|分钟|ms|QPS|单|元|美元)"
)

EN_STOPWORDS = {
    "about", "able", "all", "also", "and", "any", "are", "as", "at", "be",
    "benefits", "bonus", "boot", "build", "built", "business", "but", "by",
    "can", "company", "compensation", "competitive", "culture", "data",
    "description", "design", "development", "e.g", "engineer", "engineering",
    "etc", "experience", "familiar", "for", "friendly", "from", "full", "good",
    "has", "have", "hiring", "i.e", "in", "into", "is", "it", "job", "junior",
    "knowledge", "manager", "may", "more", "must", "new", "nice", "not", "offer",
    "of", "office", "on", "or", "other", "our", "package", "plus", "preferred",
    "proficient", "product", "products", "qualifications", "remote",
    "requirements", "responsibilities", "role", "senior", "service", "services",
    "shanghai", "should", "skill", "skills", "stack", "strong", "system",
    "systems", "team", "teams", "than", "that", "the", "their", "they", "this",
    "to", "use", "used", "using", "we", "will", "with", "work", "working",
    "year", "years", "your",
}

BONUS_SECTION_MARKERS = ("加分项", "加分", "优先条件", "nice to have", "preferred qualifications")
ELIGIBLE_SECTION_MARKERS = (
    "任职要求", "岗位要求", "职位要求", "任职资格", "加分项", "你需要",
    "requirements", "qualifications", "preferred qualifications", "nice to have",
)
NON_ELIGIBLE_SECTION_MARKERS = (
    "岗位职责", "工作职责", "职责", "公司介绍", "团队介绍", "关于我们",
    "福利", "薪资", "工作地点", "responsibilities", "about us", "benefits",
)
BONUS_LINE_MARKERS = ("加分", "优先", "最好", "了解", "nice to have", "plus", "preferred", "bonus")
ELIGIBLE_LINE_MARKERS = ("加分", "优先", "最好", "nice to have", "plus", "preferred", "bonus")


def is_ascii(text):
    return all(ord(char) < 128 for char in text)


def contains(keyword, text):
    lowered = text.lower()
    for variant in keyword_variants(keyword):
        if is_ascii(variant):
            forms, ignore_case = _variant_forms(variant)
            if any(
                _token_pattern(form, ignore_case).search(text) for form in forms
            ):
                return True
        elif variant.lower() in lowered:
            return True
    return False


def count_occurrences(keyword, text):
    total = 0
    for variant in keyword_variants(keyword):
        if is_ascii(variant):
            forms, ignore_case = _variant_forms(variant)
            for form in forms:
                total += len(_token_pattern(form, ignore_case).findall(text))
        else:
            total += text.lower().count(variant.lower())
    return total


def classify_lines(lines):
    """返回 [(line, is_bonus, in_eligible_section)]。"""
    current_bonus = False
    current_eligible = False
    output = []
    for line in lines:
        low = line.lower()
        if any(marker in low for marker in BONUS_SECTION_MARKERS):
            current_bonus = True
            current_eligible = True
        elif any(marker in low for marker in ELIGIBLE_SECTION_MARKERS):
            current_bonus = False
            current_eligible = True
        elif any(marker in low for marker in NON_ELIGIBLE_SECTION_MARKERS):
            current_bonus = False
            current_eligible = False
        is_bonus = current_bonus
        if any(marker in low for marker in BONUS_LINE_MARKERS):
            is_bonus = True
        line_eligible = current_eligible or any(
            marker in low for marker in ELIGIBLE_LINE_MARKERS
        )
        output.append((line, is_bonus, line_eligible))
    return output


def _has_token_signal(raw):
    """含大写字母、数字或点的 token 自带技术信号。"""
    return any(char.isupper() or char.isdigit() or char == "." for char in raw)


def extract_keywords(jd_text, lines):
    """从 JD 中抽取词典词与英文技术词，按同义词 canonical 去重。"""
    eligible_lines = [line for line, _, eligible in lines if eligible]
    keywords = {}
    for term in TERMS:
        if contains(term, jd_text):
            canonical = canonical_key(term)
            display = (
                SYNONYM_GROUPS[canonical][0]
                if canonical in SYNONYM_GROUPS
                else term
            )
            keywords.setdefault(canonical, display)
    for match in EN_TOKEN_RE.finditer(jd_text):
        raw = match.group(0).strip("._- ")
        low = raw.lower()
        if len(low) < 2 or low.isdigit() or low in EN_STOPWORDS:
            continue
        if len(low) <= 2 and low in SHORT_TOKEN_CASE_FORMS:
            if raw not in SHORT_TOKEN_CASE_FORMS[low]:
                continue
        if not _has_token_signal(raw):
            if not any(contains(raw, line) for line in eligible_lines):
                continue
        canonical = canonical_key(low)
        display = (
            SYNONYM_GROUPS[canonical][0]
            if canonical in SYNONYM_GROUPS
            else raw
        )
        keywords.setdefault(canonical, display)
    return keywords


def score(jd_text, resume_text):
    lines = classify_lines(jd_text.splitlines() or [jd_text])
    keywords = extract_keywords(jd_text, lines)
    if not keywords:
        raise ValueError("未能从 JD 中识别出任何关键词")

    required = []
    bonus = []
    for low, display in sorted(keywords.items(), key=lambda item: item[1].lower()):
        occurrences = count_occurrences(display, resume_text)
        hits = [
            is_bonus
            for line, is_bonus, _ in lines
            if contains(display, line)
        ]
        if not hits:
            hits = [False]
        item = {
            "keyword": display,
            "matched": occurrences > 0,
            "occurrences": occurrences,
        }
        if all(hits):
            bonus.append(item)
        else:
            required.append(item)

    req_matched = sum(1 for item in required if item["matched"])
    bonus_matched = sum(1 for item in bonus if item["matched"])
    req_total = len(required)
    bonus_total = len(bonus)

    req_pct = (100.0 * req_matched / req_total) if req_total else 100.0
    bonus_pct = (100.0 * bonus_matched / bonus_total) if bonus_total else 0.0
    coverage = (
        0.8 * req_pct + 0.2 * bonus_pct if bonus_total else req_pct
    )

    matched_occurrences = [
        item["occurrences"] for item in required + bonus if item["matched"]
    ]
    if matched_occurrences:
        average = sum(matched_occurrences) / float(len(matched_occurrences))
        depth = min(100.0, 40.0 + 15.0 * min(average, 4.0))
    else:
        depth = 0.0

    quantified = len(METRIC_RE.findall(resume_text))
    evidence = min(100.0, 25.0 * min(quantified, 4))

    overall = round(0.5 * coverage + 0.25 * depth + 0.25 * evidence, 1)
    if req_pct >= 75 and overall >= 65:
        verdict = "可投"
    elif req_pct >= 45:
        verdict = "需补"
    else:
        verdict = "不建议"

    return {
        "verdict": verdict,
        "overall": overall,
        "dimensions": {
            "coverage": round(coverage, 1),
            "depth": round(depth, 1),
            "evidence": round(evidence, 1),
        },
        "coverage": {
            "required": {
                "matched": req_matched,
                "total": req_total,
                "percent": round(req_pct, 1),
            },
            "bonus": {
                "matched": bonus_matched,
                "total": bonus_total,
                "percent": round(bonus_pct, 1),
            },
        },
        "gaps": {
            "required": [item["keyword"] for item in required if not item["matched"]],
            "bonus": [item["keyword"] for item in bonus if not item["matched"]],
        },
        "keywords": {"required": required, "bonus": bonus},
        "stats": {
            "jdChars": len(jd_text),
            "resumeChars": len(resume_text),
            "quantified": quantified,
        },
    }


def read_source(source):
    if source == "-":
        return sys.stdin.read()
    with open(source, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def render(result):
    lines = [
        "JD-简历匹配度报告",
        "=" * 48,
        "结论：%s    综合分：%.1f" % (result["verdict"], result["overall"]),
        "覆盖：%.1f    深度：%.1f    证据：%.1f"
        % (
            result["dimensions"]["coverage"],
            result["dimensions"]["depth"],
            result["dimensions"]["evidence"],
        ),
        "必须项：%d/%d（%.1f%%）    加分项：%d/%d（%.1f%%）"
        % (
            result["coverage"]["required"]["matched"],
            result["coverage"]["required"]["total"],
            result["coverage"]["required"]["percent"],
            result["coverage"]["bonus"]["matched"],
            result["coverage"]["bonus"]["total"],
            result["coverage"]["bonus"]["percent"],
        ),
    ]
    required_gaps = result["gaps"]["required"]
    bonus_gaps = result["gaps"]["bonus"]
    lines.append("必须项缺口：" + ("、".join(required_gaps) if required_gaps else "无"))
    lines.append("加分项缺口：" + ("、".join(bonus_gaps) if bonus_gaps else "无"))
    lines.append("量化表达：%d 处" % result["stats"]["quantified"])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="计算 JD 与简历的关键词覆盖、缺口与三维分数。",
        epilog="示例：python3 jd_match.py --jd jd.txt --resume resume.txt --json",
    )
    parser.add_argument("paths", nargs="*", help="位置参数：JD 文件 简历文件")
    parser.add_argument("--jd", help="JD 文件路径，- 表示 stdin")
    parser.add_argument("--resume", help="简历文件路径，- 表示 stdin")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)

    if args.jd or args.resume:
        if not (args.jd and args.resume):
            parser.error("--jd 与 --resume 必须同时提供")
        jd_source, resume_source = args.jd, args.resume
    else:
        if len(args.paths) != 2:
            parser.error("需要 JD 与简历两个输入（位置参数或 --jd/--resume）")
        jd_source, resume_source = args.paths

    if jd_source == "-" and resume_source == "-":
        parser.error("JD 与简历不能同时从 stdin 读取")

    try:
        jd_text = read_source(jd_source)
        resume_text = read_source(resume_source)
    except OSError as exc:
        print("错误：无法读取输入：%s" % exc, file=sys.stderr)
        return 2

    if not jd_text.strip():
        print("错误：JD 为空，无法计算匹配度。", file=sys.stderr)
        return 2
    try:
        result = score(jd_text, resume_text)
    except ValueError as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 2
    if args.json:
        payload = dict(result)
        payload["ok"] = True
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return 0

    print(render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
