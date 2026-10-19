"""评测层数据模型：用例 / 用例结果 / 报告（JSON 序列化 + 通过率 + judge 均分）。

EvalCase 由 Skill 包 evals/cases.py 的 CASES 列表项构造（from_dict 校验必填与未知键）。
三层评测共用同一套结果/报告形态：outcome（产出）/ behavior（单轮行为）/ guardrail（负向门禁）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

LAYERS = ("outcome", "behavior", "guardrail")

_ALLOWED_KEYS = {
    "name",
    "layer",
    "input",
    "must_contain",
    "judge_rubric",
    "expect_skill",
    "script",
    "expect_guardrails",
}


@dataclass
class EvalCase:
    skill: str
    name: str
    layer: str
    input: str
    must_contain: list[str] = field(default_factory=list)
    judge_rubric: list[str] = field(default_factory=list)
    expect_skill: str = ""
    script: list[dict] = field(default_factory=list)
    expect_guardrails: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, skill: str, raw: dict) -> EvalCase:
        unknown = set(raw) - _ALLOWED_KEYS
        if unknown:
            raise ValueError(f"评测用例含未知字段 {sorted(unknown)}（拼写错误？）: {raw!r}")
        for key in ("name", "layer", "input"):
            if not raw.get(key):
                raise ValueError(f"评测用例缺少必填字段 {key}: {raw!r}")
        if raw["layer"] not in LAYERS:
            raise ValueError(f"未知评测层 {raw['layer']!r}（应为 {'/'.join(LAYERS)}）")
        return cls(
            skill=skill,
            name=raw["name"],
            layer=raw["layer"],
            input=raw["input"],
            must_contain=list(raw.get("must_contain", [])),
            judge_rubric=list(raw.get("judge_rubric", [])),
            expect_skill=raw.get("expect_skill", ""),
            script=list(raw.get("script", [])),
            expect_guardrails=list(raw.get("expect_guardrails", [])),
        )


@dataclass
class CaseResult:
    skill: str
    case: str
    layer: str
    status: str  # pass | fail | error | skipped
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "skill": self.skill,
            "case": self.case,
            "layer": self.layer,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class EvalReport:
    results: list[CaseResult] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))

    def pass_rate(self) -> float:
        """通过率；skipped 不进分母（无 API key 时 outcome/behavior 全 skipped，不算 0 分）。"""
        counted = [r for r in self.results if r.status != "skipped"]
        if not counted:
            return 1.0
        return sum(1 for r in counted if r.status == "pass") / len(counted)

    def judge_avg(self) -> float | None:
        """全体 outcome 用例的有效 judge 均分；无有效分返回 None。"""
        scores = [
            r.detail["judge_avg"]
            for r in self.results
            if r.layer == "outcome" and r.detail.get("judge_valid")
        ]
        if not scores:
            return None
        return sum(scores) / len(scores)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "pass_rate": round(self.pass_rate(), 4),
            "judge_avg": self.judge_avg(),
            "results": [r.to_dict() for r in self.results],
        }

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> EvalReport:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            results=[CaseResult(**r) for r in data.get("results", [])],
            generated_at=data.get("generated_at", ""),
        )
