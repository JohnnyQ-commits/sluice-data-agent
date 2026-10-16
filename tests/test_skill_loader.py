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
