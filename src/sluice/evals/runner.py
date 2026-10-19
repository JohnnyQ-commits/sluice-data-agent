"""三层评测执行器：guardrail（确定性）/ behavior（单轮）/ outcome（完整循环）。

评测引擎自身不依赖真实 LLM：runner 接受 client 参数，测试注入桩。
pytest 套件保持零 API key 可跑（P0 以来的红线）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config.settings import Settings
from ..exceptions import BudgetExceededError, RoundsExhaustedError, SluiceError
from ..guardrails.chain import GuardrailChain
from ..llm.types import LLMResponse, Message, TextBlock, ToolUseBlock
from ..runtime.loop import ReActRuntime
from ..runtime.prompt import build_system
from ..tools.registry import ToolRegistry
from .model import CaseResult, EvalCase

if TYPE_CHECKING:
    from ..llm.client import LLMClient
    from ..skills.loader import SkillPackage

EVAL_MAX_ROUNDS = 8  # 门禁用例走完 拦截→自纠→final 足够，轮次上限防剧本行为失控
_NO_KEY_REASON = "需要 SLUICE_LLM_API_KEY"  # behavior/outcome 无 client 时的 skipped 原因


class ScriptExhaustedError(SluiceError):
    """评测剧本耗尽（空剧本 / 末条回放额度用完）：script 与运行时行为不符。"""


def _response_from_spec(spec: dict) -> LLMResponse:
    """script 条目 → LLMResponse：{"tool","args"} → 工具调用；{"text"} → 最终回答。

    形状白名单校验：条目非 dict（TypeError）或键组合不在 {"tool"}/{"tool","args"}/{"text"} 内
    （ValueError）直接报错（病因清晰），坏用例由 run_case 兜底归 error。
    """
    if not isinstance(spec, dict):
        raise TypeError(f"script 条目应为 dict，收到 {type(spec).__name__}: {spec!r}")
    keys = set(spec)
    if "tool" in keys:
        unknown = keys - {"tool", "args"}
        if unknown:
            raise ValueError(f"script 工具条目含未知键 {sorted(unknown)}: {spec!r}")
        return LLMResponse(
            content=[ToolUseBlock("t1", spec["tool"], spec.get("args", {}))],
            stop_reason="tool_use",
            usage={},
        )
    if keys == {"text"}:
        return LLMResponse(content=[TextBlock(spec["text"])], stop_reason="end_turn", usage={})
    raise ValueError(
        f"script 条目键组合非法（应 {{'tool'}}/{{'tool','args'}}/{{'text'}}): {spec!r}"
    )


class ScriptedClient:
    """评测剧本回放（与 tests 同款模式，guardrail 层负向用例专用）。

    回放仅可能被输出自修轮消费，语义=模型复读（复检仍不过只产 degraded，产不出 repaired）。
    末条最多回放 1 次，再次耗尽抛 ScriptExhaustedError：缺 final 的剧本归 error
    而不是在断言处炸掉评测引擎，也不会靠无限回放伪造自修轮事件制造假 pass。
    calls 记录每次 chat 摘要（behavior 层复用本构件时免改）。
    """

    def __init__(self, responses: list[LLMResponse]):
        self._script = list(responses)
        self._last: LLMResponse | None = None
        self._replays_left = 1
        self.calls: list[dict] = []

    def chat(self, messages, system=None, tools=None):
        self.calls.append({"messages": len(messages), "system": system, "tools": tools})
        if self._script:
            self._last = self._script.pop(0)
        elif self._last is None:
            raise ScriptExhaustedError("评测剧本为空：script 与运行时行为不符")
        elif self._replays_left:
            self._replays_left -= 1
        else:
            raise ScriptExhaustedError("评测剧本耗尽（末条回放限 1 次）：script 与运行时行为不符")
        return self._last


class GuardrailRunner:
    """③ 负向门禁评测：剧本驱动完整 ReAct 循环，断言门禁事件真的发生。确定性、零 LLM 成本。

    匹配语义：expect_guardrails 各项须恰为 {"gate","action"} 两键，与事件按相等匹配；
    期望项全部命中即 pass，多余事件不扣分。
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    def run_case(self, case: EvalCase, registry: ToolRegistry) -> CaseResult:
        chain = GuardrailChain(self._settings, [])  # 无契约：此层只测拦截，不测产出
        events: list[dict] = []
        try:
            client = ScriptedClient([_response_from_spec(s) for s in case.script])
            rt = ReActRuntime(client, registry, max_rounds=EVAL_MAX_ROUNDS, guardrails=chain)
            rt.run(case.input, on_event=events.append)
        except SluiceError as exc:  # 轮次耗尽/超预算/剧本耗尽 = 剧本与行为不符或门禁终止
            return CaseResult(case.skill, case.name, case.layer, "error", {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — 坏用例兜底：单个用例归 error，不炸整批评测
            return CaseResult(
                case.skill,
                case.name,
                case.layer,
                "error",
                {"error": f"{type(exc).__name__}: {exc}"},
            )
        got = [
            {"gate": e["gate"], "action": e["action"]} for e in events if e["type"] == "guardrail"
        ]
        missing = [x for x in case.expect_guardrails if x not in got]
        return CaseResult(
            case.skill,
            case.name,
            case.layer,
            "pass" if not missing else "fail",
            {"expected": case.expect_guardrails, "got": got},
        )


class BehaviorRunner:
    """② 单轮行为评测：一次 chat 调用（无 ReAct 循环），断言意图→Skill 路由与工具选择。

    真实 LLM 下每用例仅一次 API 调用，秒级回归；测试用桩 client 注入。
    """

    def __init__(self, client: LLMClient):
        self._client = client

    def run_case(
        self, case: EvalCase, packages: list[SkillPackage], registry: ToolRegistry
    ) -> CaseResult:
        try:
            response = self._client.chat(
                [Message("user", [TextBlock(case.input)])],
                system=build_system(packages),
                tools=registry.to_defs(),
            )
            used = [tu.name for tu in response.tool_uses]
            # 内置无点工具不算 skill 命中（仍计入 used）
            prefixes = {n.split(".", 1)[0] for n in used if "." in n}
            ok = (case.expect_skill in prefixes) if case.expect_skill else not used
        except SluiceError as exc:  # 网关等已知错误 = 用例归 error，不炸整批评测
            return CaseResult(case.skill, case.name, case.layer, "error", {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — 坏用例兜底：单个用例归 error，不炸整批评测
            return CaseResult(
                case.skill,
                case.name,
                case.layer,
                "error",
                {"error": f"{type(exc).__name__}: {exc}"},
            )
        return CaseResult(
            case.skill,
            case.name,
            case.layer,
            "pass" if ok else "fail",
            {"used_tools": used, "expect_skill": case.expect_skill},
        )


class OutcomeRunner:
    """① 产出评测：完整 ReAct 循环 + 确定性断言（must_contain）。

    语义断言（judge_rubric + LLM-as-judge）由 T7 接入本类。
    """

    def __init__(
        self, settings: Settings, client: LLMClient, judge_client: LLMClient | None = None
    ):
        self._settings = settings
        self._client = client
        self._judge_client = judge_client

    def run_case(self, case: EvalCase, package: SkillPackage, registry: ToolRegistry) -> CaseResult:
        chain = GuardrailChain(self._settings, [package])
        rt = ReActRuntime(
            self._client,
            registry,
            system=build_system([package]),
            max_rounds=self._settings.react_max_rounds,
            guardrails=chain,
        )
        events: list[dict] = []
        try:
            answer = rt.run(case.input, on_event=events.append)
        except (RoundsExhaustedError, BudgetExceededError) as exc:
            # 轮次耗尽/超预算 = 产出失败（fail 不是 error），记入产出质量
            return CaseResult(
                case.skill, case.name, case.layer, "fail", {"error": f"{type(exc).__name__}: {exc}"}
            )
        except SluiceError as exc:  # 网关故障（超时/连接/HTTP）= 评测引擎故障，不冒充质量问题
            return CaseResult(
                case.skill,
                case.name,
                case.layer,
                "error",
                {"error": f"{type(exc).__name__}: {exc}"},
            )
        except Exception as exc:  # noqa: BLE001 — 坏用例兜底：单个用例归 error，不炸整批评测
            return CaseResult(
                case.skill,
                case.name,
                case.layer,
                "error",
                {"error": f"{type(exc).__name__}: {exc}"},
            )
        missing = [s for s in case.must_contain if s not in answer]
        detail: dict = {"missing": missing, "answer_head": answer[:200]}
        if case.judge_rubric and self._judge_client is not None:
            from .judge import judge_output  # T7 落地后生效；此前 judge_rubric 用例不带 judge 跑

            items = judge_output(self._judge_client, answer, case.judge_rubric)
            valid_scores = [i["score"] for i in items if i["valid"]]
            avg = sum(valid_scores) / len(valid_scores) if valid_scores else None
            detail["judge_items"] = items
            detail["judge_avg"] = avg
            detail["judge_valid"] = bool(valid_scores)
            # judge 全无效分（防作弊判定全挂）→ 有意不产生 fail，降级为纯确定性断言
            # （must_contain 满足即 pass），与 EvalReport.judge_avg 防御口径一致，宁缺不错
            if avg is not None and avg < self._settings.eval_judge_threshold:
                missing = missing or [
                    f"judge 均分 {avg:.2f} < {self._settings.eval_judge_threshold}"
                ]
                detail["missing"] = missing  # detail 已持旧列表引用，重赋后须同步回填
        return CaseResult(
            case.skill,
            case.name,
            case.layer,
            "pass" if not missing else "fail",
            detail,
        )


def run_eval(
    selected: list[SkillPackage],
    packages: list[SkillPackage],
    registry: ToolRegistry,
    settings: Settings,
    client: LLMClient | None = None,
    judge_client: LLMClient | None = None,
) -> list[CaseResult]:
    """三层用例分发执行。client/judge_client 为 None 时对应层 skipped（无 API key 场景）。

    selected: 参评 SkillPackage 列表；packages: 全量包列表（behavior 层 system 提示要列全部 skill）。
    """
    guardrail_runner = GuardrailRunner(settings)
    behavior_runner = BehaviorRunner(client) if client is not None else None
    outcome_runner = OutcomeRunner(settings, client, judge_client) if client is not None else None
    results: list[CaseResult] = []
    for pkg in selected:
        for raw in pkg.evals:
            if not isinstance(raw, dict):
                results.append(
                    CaseResult(
                        pkg.name,
                        "?",
                        "invalid",
                        "error",
                        {"error": f"评测用例应为 dict，收到 {type(raw).__name__}: {raw!r}"},
                    )
                )
                continue
            try:
                case = EvalCase.from_dict(pkg.name, raw)
            except ValueError as exc:
                results.append(
                    CaseResult(
                        pkg.name, str(raw.get("name", "?")), "invalid", "error", {"error": str(exc)}
                    )
                )
                continue
            if case.layer == "guardrail":
                results.append(guardrail_runner.run_case(case, registry))
            elif case.layer == "behavior":
                if behavior_runner is None:
                    results.append(
                        CaseResult(
                            pkg.name,
                            case.name,
                            case.layer,
                            "skipped",
                            {"reason": _NO_KEY_REASON},
                        )
                    )
                else:
                    results.append(behavior_runner.run_case(case, packages, registry))
            else:
                if outcome_runner is None:
                    results.append(
                        CaseResult(
                            pkg.name,
                            case.name,
                            case.layer,
                            "skipped",
                            {"reason": _NO_KEY_REASON},
                        )
                    )
                else:
                    results.append(outcome_runner.run_case(case, pkg, registry))
    return results
