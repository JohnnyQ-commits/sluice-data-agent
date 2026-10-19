"""LLM-as-judge 测试：rubric 打分 / 引用防作弊 / 容错降级。"""

from sluice.evals.judge import _normalize, judge_output
from sluice.llm.types import LLMResponse, TextBlock


class StubJudge:
    def __init__(self, reply):
        self._reply = reply

    def chat(self, messages, system=None, tools=None):
        return LLMResponse(content=[TextBlock(self._reply)], stop_reason="end_turn", usage={})


ANSWER = "## 核心指标\n上周销售额 123.45 万元，环比 +15%\n## 重点发现\n杭州连续三周居首"


def test_valid_quote_scores():
    reply = (
        '{"items": [{"item": "数值一致", "score": 5, "quote": "上周销售额 123.45 万元"},'
        ' {"item": "口径正确", "score": 4, "quote": "环比 +15%"}]}'
    )
    items = judge_output(StubJudge(reply), ANSWER, ["数值一致", "口径正确"])
    assert [i["score"] for i in items] == [5, 4]
    assert all(i["valid"] for i in items)


def test_quote_not_in_answer_invalid():
    """引用不到产出原文 → 判分无效（防作弊核心：judge 不能凭空给分）。"""
    reply = '{"items": [{"item": "数值一致", "score": 5, "quote": "编造的引用"}]}'
    items = judge_output(StubJudge(reply), ANSWER, ["数值一致"])
    assert items[0]["valid"] is False and items[0]["score"] is None


def test_quote_with_different_whitespace_still_valid():
    reply = '{"items": [{"item": "口径", "score": 4, "quote": "环比  +15%"}]}'  # 双空格
    items = judge_output(StubJudge(reply), ANSWER, ["口径"])
    assert items[0]["valid"] is True  # 归一化空白后命中


def test_garbage_reply_all_invalid():
    items = judge_output(StubJudge("评委今天不想说话"), ANSWER, ["数值一致"])
    assert len(items) == 1 and items[0]["valid"] is False and items[0]["score"] is None


def test_score_out_of_range_invalid():
    reply = '{"items": [{"item": "口径", "score": 9, "quote": "环比 +15%"}]}'
    items = judge_output(StubJudge(reply), ANSWER, ["口径"])
    assert items[0]["valid"] is False


def test_judge_client_exception_all_invalid():
    class ExplodingJudge:
        def chat(self, messages, system=None, tools=None):
            raise RuntimeError("网关抖动")

    items = judge_output(ExplodingJudge(), ANSWER, ["数值一致"])
    assert items[0]["valid"] is False  # judge 挂了不算用例失败，降级为无语义分


def test_normalize_collapses_whitespace():
    assert _normalize("a  b\nc") == "abc"


def test_score_string_digit_coerced():
    """真实 LLM 高频形态 "score": "4" → coerce 后有效，不判无效致 judge_avg 空转。"""
    reply = '{"items": [{"item": "口径", "score": "4", "quote": "环比 +15%"}]}'
    items = judge_output(StubJudge(reply), ANSWER, ["口径"])
    assert items[0]["valid"] is True and items[0]["score"] == 4


def test_score_whole_float_coerced():
    reply = '{"items": [{"item": "口径", "score": 4.0, "quote": "环比 +15%"}]}'
    items = judge_output(StubJudge(reply), ANSWER, ["口径"])
    assert items[0]["valid"] is True and items[0]["score"] == 4


def test_score_bool_rejected():
    """bool 守卫钉死：True/False 永不折算成分数（防后人把 isinstance 排除简化掉）。"""
    reply = '{"items": [{"item": "口径", "score": true, "quote": "环比 +15%"}]}'
    items = judge_output(StubJudge(reply), ANSWER, ["口径"])
    assert items[0]["valid"] is False and items[0]["score"] is None


def test_item_echo_with_numbering_matches():
    """judge 回显维度带序号前缀（prompt 里是 "1. 口径"）→ 剥前缀归一化后仍命中 rubric。"""
    reply = '{"items": [{"item": "1. 口径清晰", "score": 4, "quote": "环比 +15%"}]}'
    items = judge_output(StubJudge(reply), ANSWER, ["口径清晰"])
    assert items[0]["valid"] is True and items[0]["item"] == "口径清晰"


def test_markdown_fenced_json_still_parsed():
    """markdown 围栏包 JSON：贪婪 \\{.*\\} 命中围栏内对象——钉住防回归。"""
    reply = '好的，评分如下：\n```json\n{"items": [{"item": "口径", "score": 4, "quote": "环比 +15%"}]}\n```'
    items = judge_output(StubJudge(reply), ANSWER, ["口径"])
    assert items[0]["valid"] is True and items[0]["score"] == 4
