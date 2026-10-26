# my-skill-hub

零配置、零依赖的 Skill 合集。每个技能都是独立目录：一份 `SKILL.md` 负责路由与流程，`references/` 按需加载资料，`scripts/` 只依赖 Python 3.8+ 标准库，`tests/` 可直接用 `unittest` 跑通。

## 技能清单

| 名称 | slug | 一句话价值 | 分类 | 零配置 |
|---|---|---|---|---|
| 故障排查专家.Skill | `troubleshoot-expert` | 从报错、日志或告警定位根因，给出可验证修复 | it-ops-security | 是 |
| 简历面试专家.Skill | `resume-interview-expert` | JD 逐条对齐打分，改写成果句，模拟真实面试追问 | professional | 是 |
| 学习规划专家.Skill | `study-planner-expert` | 把学习目标拆成周计划与每日复习安排 | education | 是 |

## 安装与使用

技能发布到 SkillHub 后按平台方式安装；本地调试可直接把技能目录放到客户端的技能搜索路径，或用 `skillhub publish <目录> --dry-run` 做发布前自检。

```bash
# 发布前自检（不发网络请求）
skillhub publish troubleshoot-expert --dry-run

# 仓库级合规校验
python3 tools/validate_skills.py

# 跑单个技能的全部单测
python3 -m unittest discover -s troubleshoot-expert/tests -v
```

## 仓库结构

```
my-skill-hub/
├── LICENSE
├── README.md
├── tools/
│   └── validate_skills.py        # 仓库级发布前校验
├── troubleshoot-expert/
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   ├── references/               # 按主题拆分的排查资料
│   ├── scripts/                  # 零依赖排查脚本
│   └── tests/                    # unittest + 纯文本夹具
├── resume-interview-expert/
└── study-planner-expert/
```

每个技能都遵守同一套约束：

- `SKILL.md` frontmatter 扁平（只允许 `key: value` / `key: [a, b]`），不使用 `metadata:` 嵌套。
- `description` 单行、双引号包裹。
- 脚本只用 Python 标准库，统一支持 `--help` 与 `--json`。
- 仓库不提交二进制文件，测试夹具全部是纯文本。
- 技能之间内容独立，不复制粘贴。

## 校验

`tools/validate_skills.py` 会扫描仓库内所有含 `SKILL.md` 的目录，检查 frontmatter、slug、SemVer、文件体积、二进制、references 引用、常见密钥模式、规定章节，以及是否混入 `.omc/`、`.claude/`、`.venv/`、`__pycache__/` 等运行时产物；任何一项失败退出码为 1。

> 单测会生成 `__pycache__/`，发布或校验前请先清理；也可以在运行测试与校验时设置 `PYTHONDONTWRITEBYTECODE=1`。

```bash
python3 tools/validate_skills.py --json
```

## License

[MIT](LICENSE)
