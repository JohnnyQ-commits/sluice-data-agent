"""build_system 单测：Skill 清单 + 前缀工具强引导句（裸工具绕过契约实录的钉子）。"""

from pathlib import Path

from sluice.runtime.prompt import build_system
from sluice.skills.loader import SkillPackage


def _pkg(name: str) -> SkillPackage:
    return SkillPackage(name=name, description="测试用 Skill", path=Path("."))


def test_build_system_with_packages_contains_prefix_steering():
    """有 Skill 时必须点名：skill 职责走带前缀工具，裸工具不承接（不携带契约）。"""
    text = build_system([_pkg("weekly-report")])
    assert "- weekly-report: 测试用 Skill" in text
    assert "带前缀工具" in text
    assert "裸工具" in text
    assert "不携带" in text and "产出契约" in text


def test_build_system_without_packages_no_steering():
    """无 Skill 时保持极简基底，不出现引导句与 Skill 段落。"""
    text = build_system([])
    assert "可用的 Skill" not in text
    assert "带前缀工具" not in text
