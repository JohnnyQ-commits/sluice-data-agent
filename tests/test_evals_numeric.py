"""确定性数值通道测试（备案①）：expect_values 的 SQL 真值对账，零 LLM 成本。"""

import sqlite3

import pytest
from test_evals_runner import _package, _registry  # 同目录顶层导入：tests/ 无 __init__.py，CI 上 'tests' 不是包

from sluice.config.settings import Settings
from sluice.evals.model import EvalCase
from sluice.evals.numeric import check_value_in_answer, verify_expect_values
from sluice.evals.runner import OutcomeRunner
from sluice.llm.types import LLMResponse, TextBlock, ToolUseBlock

# ---------- 匹配语义 ----------


@pytest.mark.parametrize(
    ("value", "answer", "expected"),
    [
        (311, "周活跃用户数 (WAU)：311（环比 +16.5%）", True),
        (311, "WAU 311 人", True),
        (311.0, "WAU：311.0", True),
        (311, "WAU 1311", False),  # 词边界：子串不算命中
        (311, "WAU 3115", False),
        (174.0, "日均 DAU 174.0", True),
        (174.0, "日均 DAU 174", True),  # 整值接受整写法
        (174.0, "日均 DAU 175", False),
        (16.5, "环比 +16.5%", True),
        (16.5, "环比 +16.52%", False),  # 1 位小数候选不被 2 位小数吞
        (8042.34, "总销售额 8,042.34（环比 +15%）", True),  # 千分位剥除
        (213177.33, "总销售额 21.32万", True),  # 中文"万"缩写（2 位小数）
        (213177.33, "总销售额 21.3万", True),  # "万"缩写（1 位小数）
        (213177.33, "总销售额 213177.33", True),  # 原始全值
        (213177.33, "总销售额 213,177.33", True),  # 全值+千分位
        (213177.33, "总销售额 22.5万", False),
        (-3.5, "环比 -3.5%", True),  # 负值
    ],
)
def test_check_value_in_answer(value, answer, expected):
    assert check_value_in_answer(value, answer) is expected


# ---------- verify_expect_values ----------


@pytest.fixture()
def demo_db(tmp_path):
    db = tmp_path / "d.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sales (sale_date TEXT, city TEXT, amount REAL)")
    conn.executemany(
        "INSERT INTO sales VALUES (?,?,?)",
        [("2026-01-13", "杭州", 100.0), ("2026-01-13", "北京", 200.0)],
    )
    conn.commit()
    conn.close()
    return str(db)


def test_verify_expect_values_pass_and_fail(demo_db):
    results = verify_expect_values(
        demo_db,
        [
            {"label": "销售总额", "sql": "SELECT SUM(amount) FROM sales"},
            {"label": "缺失值", "sql": "SELECT SUM(amount) FROM sales WHERE city='广州'"},
        ],
        "总销售额 300.0",
    )
    assert results[0] == {"label": "销售总额", "expected": 300.0, "found": True}
    assert results[1]["found"] is False and "为空" in results[1]["error"]


def test_verify_expect_values_sql_error(demo_db):
    results = verify_expect_values(
        demo_db, [{"label": "坏 SQL", "sql": "SELECT * FROM no_such_table"}], "随便"
    )
    assert results[0]["found"] is False and "SQL 执行失败" in results[0]["error"]


def test_verify_expect_values_non_numeric(demo_db):
    results = verify_expect_values(
        demo_db, [{"label": "文本值", "sql": "SELECT city FROM sales LIMIT 1"}], "杭州"
    )
    assert results[0]["found"] is False and "不是数值" in results[0]["error"]


# ---------- EvalCase 校验 ----------


def test_eval_case_accepts_expect_values():
    case = EvalCase.from_dict(
        "s",
        {
            "name": "c",
            "layer": "outcome",
            "input": "i",
            "expect_values": [{"label": "L", "sql": "SELECT 1"}],
        },
    )
    assert case.expect_values == [{"label": "L", "sql": "SELECT 1"}]


@pytest.mark.parametrize(
    "raw",
    [
        {"expect_values": "not-a-list"},
        {"expect_values": ["no-dict"]},
        {"expect_values": [{"label": "", "sql": "SELECT 1"}]},
        {"expect_values": [{"label": "L"}]},  # 缺 sql
        {"expect_values": [{"sql": "SELECT 1"}]},  # 缺 label
    ],
)
def test_eval_case_rejects_bad_expect_values(raw):
    with pytest.raises(ValueError, match="expect_values"):
        EvalCase.from_dict("s", {"name": "c", "layer": "outcome", "input": "i", **raw})


# ---------- OutcomeRunner 集成 ----------


class Scripted:
    def __init__(self, responses):
        self._responses = list(responses)

    def chat(self, messages, system=None, tools=None):
        return self._responses.pop(0)


def _outcome_client(answer: str):
    return Scripted(
        [
            LLMResponse(
                content=[ToolUseBlock("t1", "alpha.query", {"sql": "SELECT 1"})],
                stop_reason="tool_use",
                usage={},
            ),
            LLMResponse(content=[TextBlock(answer)], stop_reason="end_turn", usage={}),
        ]
    )


def test_outcome_runner_expect_values_pass(tmp_path, demo_db):
    case = EvalCase.from_dict(
        "alpha",
        {
            "name": "c",
            "layer": "outcome",
            "input": "出报表",
            "must_contain": ["指标"],
            "expect_values": [{"label": "销售总额", "sql": "SELECT SUM(amount) FROM sales"}],
        },
    )
    result = OutcomeRunner(
        Settings(audit_dir=str(tmp_path / "a"), demo_db_path=demo_db),
        _outcome_client("## 指标\n总销售额 300.0"),
    ).run_case(case, _package(), _registry())
    assert result.status == "pass"
    assert result.detail["expect_values"][0]["found"] is True


def test_outcome_runner_expect_values_fail(tmp_path, demo_db):
    """数值编造（产出写了库中不存在的数）被确定性通道当场抓住——judge 骗不过查库。"""
    case = EvalCase.from_dict(
        "alpha",
        {
            "name": "c",
            "layer": "outcome",
            "input": "出报表",
            "must_contain": ["指标"],
            "expect_values": [{"label": "销售总额", "sql": "SELECT SUM(amount) FROM sales"}],
        },
    )
    result = OutcomeRunner(
        Settings(audit_dir=str(tmp_path / "a"), demo_db_path=demo_db),
        _outcome_client("## 指标\n总销售额 999.9"),
    ).run_case(case, _package(), _registry())
    assert result.status == "fail"
    assert any("销售总额" in m for m in result.detail["missing"])
    assert result.detail["expect_values"][0]["expected"] == 300.0


def test_outcome_runner_expect_values_fail_does_not_mask_must_contain(tmp_path, demo_db):
    """must_contain 与 expect_values 的 missing 合并展示，不互相吞。"""
    case = EvalCase.from_dict(
        "alpha",
        {
            "name": "c",
            "layer": "outcome",
            "input": "出报表",
            "must_contain": ["指标", "发现"],
            "expect_values": [{"label": "销售总额", "sql": "SELECT SUM(amount) FROM sales"}],
        },
    )
    result = OutcomeRunner(
        Settings(audit_dir=str(tmp_path / "a"), demo_db_path=demo_db), _outcome_client("什么都没有")
    ).run_case(case, _package(), _registry())
    assert result.status == "fail"
    assert "指标" in result.detail["missing"] and "发现" in result.detail["missing"]
    assert any("销售总额" in m for m in result.detail["missing"])
