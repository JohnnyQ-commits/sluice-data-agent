"""tests/test_output_gate.py"""
from sluice.guardrails.output_gate import check_output


def test_empty_answer_rejected():
    assert check_output("", []) is not None
    assert check_output("   \n  ", []) is not None


def test_missing_section_rejected():
    reason = check_output("# 周报\n## 核心指标\n正文", ["核心指标", "重点发现"])
    assert reason is not None and "重点发现" in reason and "核心指标" not in reason


def test_missing_sections_plural():
    reason = check_output("随便", ["核心指标", "重点发现"])
    assert "核心指标" in reason and "重点发现" in reason


def test_pass_with_sections():
    assert check_output("## 核心指标\nx\n## 重点发现\ny", ["核心指标", "重点发现"]) is None


def test_pass_without_contract():
    assert check_output("任意非空回答", []) is None
