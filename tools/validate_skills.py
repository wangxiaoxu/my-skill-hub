#!/usr/bin/env python3
"""SkillHub 仓库级发布前校验。

扫描仓库内所有含 SKILL.md 的目录，逐项检查：
  - frontmatter 扁平且必填键齐备，未使用 metadata 嵌套
  - slug 为 kebab-case，version 为合法 SemVer
  - description 单行、双引号包裹、不含裸冒号
  - 文件数 / 总体积 / 单文件体积 / 二进制文件
  - SKILL.md 规定的 6 个二级标题
  - 路由表里提到的 references 文件真实存在
  - 常见密钥模式
  - agents/openai.yaml 存在且 default_prompt 含 $<slug>

只依赖 Python 3.8+ 标准库。

用法：
    python3 tools/validate_skills.py
    python3 tools/validate_skills.py --root . --json
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

REQUIRED_KEYS = [
    "name",
    "slug",
    "displayName",
    "version",
    "summary",
    "description",
    "tags",
    "license",
    "homepage",
]

REQUIRED_SECTIONS = [
    "## 执行原则",
    "## 任务路由",
    "## 主流程",
    "## 验收证据",
    "## 硬规则",
    "## 兼容性",
]

SLUG_RE = re.compile(r"^[a-z0-9-]+$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
REFERENCE_RE = re.compile(r"references/([A-Za-z0-9._-]+\.md)")

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff",
    ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".tar",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".class", ".jar",
    ".pyc", ".pyo", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".mov", ".avi", ".wav", ".flac", ".ogg", ".webm",
    ".sqlite", ".db", ".parquet", ".xlsx", ".xls", ".docx", ".doc", ".pptx",
}

TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".py", ".yaml", ".yml", ".json",
    ".toml", ".cfg", ".ini", ".sh", ".bash", ".csv", ".html", ".css", ".js",
    ".log", ".text",
}

SKIP_DIRS = {
    ".git", ".omc", ".idea", ".vscode", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".codegraph", ".venv", "venv",
}

SECRET_PATTERNS = [
    ("OpenAI 风格 key", re.compile(r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{20,}")),
    ("GitHub token", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("私钥头", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

PLACEHOLDER_PATTERNS = [
    ("TODO 标记", re.compile(r"\bTODO\b")),
    ("FIXME 标记", re.compile(r"\bFIXME\b")),
    ("占位符", re.compile(r"<placeholder>", re.IGNORECASE)),
]

MAX_FILES = 200
MAX_TOTAL_BYTES = 10 * 1024 * 1024
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_SKILL_MD_BYTES = 12 * 1024


def iter_skill_dirs(root):
    """返回 root 下所有含 SKILL.md 的目录（按路径排序）。"""
    found = []
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        )
        if "SKILL.md" in filenames:
            found.append(Path(dirpath))
    return sorted(found)


def strip_scalar(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_frontmatter(text):
    """解析扁平 frontmatter，返回 (data, raw_lines, errors)。"""
    lines = text.split("\n")
    errors = []
    if not lines or lines[0].strip() != "---":
        return None, [], ["SKILL.md 首行必须是 ---"]
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx < 0:
        return None, [], ["frontmatter 缺少结束标记 ---"]

    raw_lines = lines[1:end_idx]
    data = {}
    for raw in raw_lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[0] in (" ", "\t"):
            errors.append("frontmatter 出现缩进行（疑似 metadata 嵌套）：%r" % raw)
            continue
        if ":" not in raw:
            errors.append("frontmatter 行缺少冒号：%r" % raw)
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            errors.append("frontmatter 出现空键：%r" % raw)
            continue
        if key in data:
            errors.append("frontmatter 重复键：%s" % key)
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [
                strip_scalar(item.strip())
                for item in inner.split(",")
                if item.strip()
            ]
        else:
            data[key] = strip_scalar(value)
    return data, raw_lines, errors


def is_binary(path, sample):
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    return b"\x00" in sample


def collect_files(skill_dir):
    files = []
    for dirpath, dirnames, filenames in os.walk(str(skill_dir)):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_symlink() or name == ".DS_Store":
                continue
            files.append(path)
    return files


def check_skill(skill_dir, repo_root):
    errors = []
    warnings = []
    skill_md = skill_dir / "SKILL.md"

    try:
        skill_text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return ["读取 SKILL.md 失败：%s" % exc], warnings, {}

    if len(skill_text.encode("utf-8")) > MAX_SKILL_MD_BYTES:
        errors.append("SKILL.md 超过 12KB 限制")

    data, raw_lines, fm_errors = parse_frontmatter(skill_text)
    errors.extend(fm_errors)
    data = data or {}

    for key in REQUIRED_KEYS:
        if key not in data or data[key] in ("", [], None):
            errors.append("frontmatter 缺少必填键：%s" % key)

    if "metadata" in data:
        errors.append("frontmatter 使用了 metadata 嵌套（CLI 不支持）")

    slug = str(data.get("slug", ""))
    if slug:
        if not SLUG_RE.match(slug):
            errors.append("slug 不是 kebab-case：%s" % slug)
        if not 2 <= len(slug) <= 128:
            errors.append("slug 长度必须在 2-128 之间：%s" % slug)
        if skill_dir.name != slug:
            errors.append(
                "目录名与 slug 不一致：目录 %s / slug %s" % (skill_dir.name, slug)
            )

    version = str(data.get("version", ""))
    if version and not SEMVER_RE.match(version):
        errors.append("version 不是合法 SemVer：%s" % version)

    if str(data.get("name", "")) not in ("", slug):
        warnings.append("name 与 slug 不一致：%s / %s" % (data.get("name"), slug))

    tags = data.get("tags")
    if not isinstance(tags, list) or not tags:
        errors.append("tags 必须是非空列表（key: [a, b] 形式）")

    description_line = None
    for raw in raw_lines:
        if raw.startswith("description:"):
            description_line = raw
            break
    if description_line is None:
        errors.append("SKILL.md 缺少 description 行")
    else:
        value_part = description_line[len("description:"):].strip()
        if not (value_part.startswith('"') and value_part.endswith('"')):
            errors.append("description 必须整行用双引号包裹")
        if ":" in value_part:
            errors.append("description 行内出现裸冒号，可能被解析器截断")
        if not value_part:
            errors.append("description 为空")

    for section in REQUIRED_SECTIONS:
        if not re.search(r"^%s\s*$" % re.escape(section), skill_text, re.MULTILINE):
            errors.append("SKILL.md 缺少二级标题：%s" % section)

    references_dir = skill_dir / "references"
    mentioned = REFERENCE_RE.findall(skill_text)
    if mentioned and not references_dir.is_dir():
        errors.append("SKILL.md 引用了 references/ 但目录不存在")
    for name in sorted(set(mentioned)):
        if not (references_dir / name).is_file():
            errors.append("路由表引用的文件不存在：references/%s" % name)

    openai_yaml = skill_dir / "agents" / "openai.yaml"
    if not openai_yaml.is_file():
        errors.append("缺少 agents/openai.yaml")
    else:
        yaml_text = openai_yaml.read_text(encoding="utf-8", errors="replace")
        for key in ("display_name", "short_description", "default_prompt"):
            if key not in yaml_text:
                errors.append("agents/openai.yaml 缺少 interface.%s" % key)
        if slug and ("$" + slug) not in yaml_text:
            errors.append("agents/openai.yaml 的 default_prompt 未引用 $%s" % slug)

    files = collect_files(skill_dir)
    if len(files) > MAX_FILES:
        errors.append("文件数 %d 超过上限 %d" % (len(files), MAX_FILES))

    total_bytes = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError as exc:
            errors.append("无法读取文件 %s：%s" % (path, exc))
            continue
        total_bytes += size
        rel = path.relative_to(skill_dir).as_posix()
        if size > MAX_FILE_BYTES:
            errors.append("单文件超过 5MB：%s" % rel)
        try:
            sample = path.read_bytes()[:8192]
        except OSError as exc:
            errors.append("读取失败 %s：%s" % (rel, exc))
            continue
        if is_binary(path, sample):
            errors.append("检测到二进制文件：%s" % rel)
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            warnings.append("未识别的文本扩展名（已跳过内容扫描）：%s" % rel)
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                errors.append("疑似硬编码密钥（%s）：%s" % (label, rel))
        for label, pattern in PLACEHOLDER_PATTERNS:
            if pattern.search(content):
                errors.append("命中%s：%s" % (label, rel))

    if total_bytes > MAX_TOTAL_BYTES:
        errors.append("技能总体积超过 10MB")

    stats = {
        "path": skill_dir.relative_to(repo_root).as_posix(),
        "slug": slug,
        "files": len(files),
        "bytes": total_bytes,
        "references": len(list(references_dir.glob("*.md")))
        if references_dir.is_dir()
        else 0,
    }
    return errors, warnings, stats


def print_report(results, as_json):
    if as_json:
        payload = {
            "ok": all(not item["errors"] for item in results),
            "skills": results,
            "skillCount": len(results),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1

    print("SkillHub 技能校验报告")
    print("=" * 48)
    if not results:
        print("未发现任何含 SKILL.md 的技能目录。")
        return 1

    failed = 0
    for item in results:
        stats = item["stats"]
        header = "%s（%s 个文件，%.1f KB）" % (
            item["slug"] or item["path"],
            stats.get("files", 0),
            stats.get("bytes", 0) / 1024.0,
        )
        if item["errors"]:
            failed += 1
            print("[FAIL] " + header)
            for err in item["errors"]:
                print("       - " + err)
        else:
            print("[OK]   " + header)
        for warning in item["warnings"]:
            print("       ! " + warning)

    print("-" * 48)
    print("共 %d 个技能，通过 %d，失败 %d" % (len(results), len(results) - failed, failed))
    return 0 if failed == 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="校验仓库内所有 Skill 的 frontmatter、体积、引用与密钥合规性。",
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent),
        help="仓库根目录（默认：本脚本上一级）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        parser.error("根目录不存在：%s" % root)

    results = []
    for skill_dir in iter_skill_dirs(root):
        errors, warnings, stats = check_skill(skill_dir, root)
        results.append(
            {
                "path": skill_dir.relative_to(root).as_posix(),
                "slug": stats.get("slug", ""),
                "stats": stats,
                "errors": errors,
                "warnings": warnings,
            }
        )

    return print_report(results, args.json)


if __name__ == "__main__":
    sys.exit(main())
