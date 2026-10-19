"""Skill 包协议 + 加载器测试（Task 2）。"""

from pathlib import Path

import pytest

from sluice.skills.loader import SkillLoader, parse_skill_md
from sluice.tools.registry import ToolRegistry

SKILL_MD = """---
name: weekly-report
description: 数据周报生成，用户提到周报/weekly report 时使用
---

# 数据周报 Skill

用 query_data 查数，用 render_report 出报告。
"""

TOOLS_PY = """from sluice.tools.base import Tool, ToolResult

class T(Tool):
    name = 'render'
    description = '渲染'
    parameters = {'type': 'object', 'properties': {}}

    def execute(self, args):
        return ToolResult('ok')

TOOLS = [T()]
"""


def make_skill_dir(tmp_path: Path) -> Path:
    d = tmp_path / "skills" / "weekly-report"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (d / "tools.py").write_text(TOOLS_PY, encoding="utf-8")
    return tmp_path / "skills"


def test_parse_skill_md_frontmatter():
    meta = parse_skill_md(SKILL_MD)
    assert meta["name"] == "weekly-report"
    assert "数据周报" in meta["description"]


def test_parse_skill_md_no_frontmatter():
    assert parse_skill_md("没有 frontmatter") == {}


def test_loader_registers_prefixed_tools(tmp_path):
    skills_dir = make_skill_dir(tmp_path)
    registry = ToolRegistry()
    packages = SkillLoader().load(skills_dir, registry)
    assert len(packages) == 1
    assert packages[0].name == "weekly-report"
    assert registry.get("weekly-report.render") is not None
    assert registry.get("render") is None  # 裸名不可见 = 注册隔离


def test_loader_skips_dir_without_skill_md(tmp_path):
    (tmp_path / "skills" / "not-a-skill").mkdir(parents=True)
    (tmp_path / "skills" / "not-a-skill" / "readme.txt").write_text("hi", encoding="utf-8")
    packages = SkillLoader().load(tmp_path / "skills", ToolRegistry())
    assert packages == []


def test_loader_missing_skills_dir_returns_empty(tmp_path):
    packages = SkillLoader().load(tmp_path / "nope", ToolRegistry())
    assert packages == []


def test_loader_tools_without_tools_py(tmp_path):
    d = tmp_path / "skills" / "bare"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    packages = SkillLoader().load(tmp_path / "skills", ToolRegistry())
    assert len(packages) == 1 and packages[0].tools == []


def test_loader_double_register_same_registry_raises(tmp_path):
    """同一 registry 二次 load 同一 skill：因 weekly-report.render 重复注册抛 ValueError（防重复挂载）。"""
    skills_dir = make_skill_dir(tmp_path)
    registry = ToolRegistry()
    SkillLoader().load(skills_dir, registry)
    with pytest.raises(ValueError, match="重复注册"):
        SkillLoader().load(skills_dir, registry)


def test_loader_reload_fresh_registry_no_leak(tmp_path):
    """新 registry 重载无串扰：各自干净，正常返回 1 个包。"""
    skills_dir = make_skill_dir(tmp_path)
    SkillLoader().load(skills_dir, ToolRegistry())
    registry2 = ToolRegistry()
    packages = SkillLoader().load(skills_dir, registry2)
    assert len(packages) == 1
    assert registry2.get("weekly-report.render") is not None


def test_loader_skmd_with_bom_still_parses(tmp_path, caplog):
    """带 UTF-8 BOM 的 SKILL.md 不应导致 frontmatter 不匹配而静默丢包。"""
    d = tmp_path / "skills" / "bommed"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8-sig")
    packages = SkillLoader().load(tmp_path / "skills", ToolRegistry())
    assert len(packages) == 1
    assert packages[0].name == "weekly-report"


def test_loader_bad_tools_py_skips_only_that_skill(tmp_path, caplog):
    """坏包（tools.py 语法错误）只跳过自身并打 warning，不殃及好包。"""
    good = tmp_path / "skills" / "weekly-report"
    good.mkdir(parents=True)
    (good / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (good / "tools.py").write_text(TOOLS_PY, encoding="utf-8")
    bad = tmp_path / "skills" / "broken-skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text(
        "---\nname: broken-skill\ndescription: 坏包\n---\n", encoding="utf-8"
    )
    (bad / "tools.py").write_text("def oops(:\n", encoding="utf-8")
    registry = ToolRegistry()
    with caplog.at_level("WARNING"):
        packages = SkillLoader().load(tmp_path / "skills", registry)
    assert [p.name for p in packages] == ["weekly-report"]
    assert registry.get("weekly-report.render") is not None
    assert any("broken-skill" in r.message for r in caplog.records)


def test_loader_all_bad_returns_empty_with_warning(tmp_path, caplog):
    """全坏时返回 [] 且有 warning 记录。"""
    bad = tmp_path / "skills" / "broken-skill"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text(
        "---\nname: broken-skill\ndescription: 坏包\n---\n", encoding="utf-8"
    )
    (bad / "tools.py").write_text("TOOLS = None\nraise RuntimeError('boom')\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        packages = SkillLoader().load(tmp_path / "skills", ToolRegistry())
    assert packages == []
    assert len(caplog.records) >= 1


def test_parse_skill_md_strips_paired_quotes():
    meta = parse_skill_md('---\nname: "weekly-report"\ndescription: \'数据周报\'\n---\n')
    assert meta["name"] == "weekly-report"
    assert meta["description"] == "数据周报"


def test_output_contract_parsed_from_frontmatter(tmp_path):
    skill = tmp_path / "s1"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: s1\ndescription: d\noutput_contract: 核心指标;重点发现\n---\n正文",
        encoding="utf-8",
    )
    pkg = SkillLoader().load(tmp_path)[0]
    assert pkg.output_contract == ["核心指标", "重点发现"]


def test_output_contract_defaults_empty(tmp_path):
    skill = tmp_path / "s2"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: s2\ndescription: d\n---\n正文", encoding="utf-8")
    assert SkillLoader().load(tmp_path)[0].output_contract == []


def test_tools_tagged_with_owner_skill(tmp_path):
    skill = tmp_path / "s3"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: s3\ndescription: d\n---\n正文", encoding="utf-8")
    (skill / "tools.py").write_text(
        "from sluice.tools.base import Tool, ToolResult\n"
        "class T(Tool):\n"
        "    name = 't'\n"
        "    description = 'x'\n"
        "    parameters = {}\n"
        "    def execute(self, args):\n"
        "        return ToolResult('ok')\n"
        "TOOLS = [T()]\n",
        encoding="utf-8",
    )
    pkg = SkillLoader().load(tmp_path)[0]
    assert pkg.tools[0].owner_skill == "s3"
