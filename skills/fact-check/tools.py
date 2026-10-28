"""报告验真 Skill 私有工具。

设计要点：验真结论的「属实/不符」盖章是**确定性工具代码**，不是 LLM 判断——
模型只负责提取声称与查真值，判定交给 utils.numeric_match 的词边界匹配（与 evals
确定性数值通道同一套实现）。这是本 Skill 的可信卖点：结论骗不了。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sluice.tools.base import Tool, ToolResult
from sluice.utils.numeric_match import check_value_in_answer

if TYPE_CHECKING:
    from sluice.tools.query_data import QueryDataTool


class FactCheckQueryTool(Tool):
    """Skill 前缀查询工具：审计员读权覆盖 sales + user_events 两域（有意放宽）。"""

    name: ClassVar[str] = "query_data"
    description: ClassVar[str] = (
        "在 demo 库执行只读 SQL（表 sales、user_events），返回行文本。验真查真值一律用本工具。"
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "只读 SELECT 语句"}},
        "required": ["sql"],
    }

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path  # None = 运行时取平台 demo 库（生产路径）
        self._kernel: QueryDataTool | None = None  # 惰性创建，避免 loader 加载阶段依赖平台工具

    def _kernel_tool(self) -> QueryDataTool:
        if self._kernel is None:
            from sluice.config.settings import Settings
            from sluice.tools.query_data import QueryDataTool

            db_path = self._db_path or Settings().demo_db_path
            self._kernel = QueryDataTool(db_path)
        return self._kernel

    def table_allowlist(self) -> list[str]:
        """静态声明本 Skill 的域内表——审计员角色跨 sales 与 user_events 两域对账。

        前缀工具名单非空，幻觉表门禁同样生效。
        """
        return ["sales", "user_events"]

    def execute(self, args: dict[str, Any]) -> ToolResult:
        sql = args.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return ToolResult("sql 必须是非空字符串", is_error=True)
        return self._kernel_tool().execute({"sql": sql})


_VERDICT_OK = "✅ 属实"
_VERDICT_BAD = "❌ 不符"
_VERDICT_UNCERTAIN = "⚠️ 存疑"


class RenderVerdictTool(Tool):
    """验真报告盖章工具：属实/不符由代码按词边界匹配判定，模型无权改写结论。"""

    name: ClassVar[str] = "render_verdict"
    description: ClassVar[str] = (
        "对账盖章：逐条比对报告声称与数据库真值（属实/不符/存疑），渲染验真报告。"
        "结论由本工具确定性判定，条目齐全后一次性调用。"
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "验真报告标题"},
            "items": {
                "type": "array",
                "description": "逐条对账明细",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string", "description": "核查项名称"},
                        "claimed": {
                            "type": "string",
                            "description": "报告原文声称（含数字的原话，不要转写）",
                        },
                        "db_value": {
                            "type": "string",
                            "description": "查库真值（SQL 返回值原样传入，查不到传空并写 note）",
                        },
                        "note": {"type": "string", "description": "备注（如数据窗口说明）"},
                    },
                    "required": ["claim", "claimed", "db_value"],
                },
            },
            "summary": {"type": "string", "description": "验真总结一句话"},
        },
        "required": ["title", "items", "summary"],
    }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        title = args.get("title")
        items = args.get("items")
        summary = args.get("summary")
        if not title or not summary:
            return ToolResult("title/summary 均为必填", is_error=True)
        if not isinstance(items, list) or not items:
            return ToolResult(
                "items 必须是非空数组（没有声称可核实时不要调用本工具）", is_error=True
            )

        rows: list[str] = []
        n_ok = n_bad = n_uncertain = 0
        for item in items:
            if not isinstance(item, dict) or not item.get("claim"):
                return ToolResult("items 元素缺少 claim", is_error=True)
            claimed = item.get("claimed")
            if not isinstance(claimed, str) or not claimed.strip():
                return ToolResult(
                    f"条目 [{item['claim']}] 缺少 claimed（报告原文声称）——必须逐字摘录，不要省略",
                    is_error=True,
                )
            db_value = item.get("db_value")
            note = item.get("note") or ""
            verdict, db_text = self._stamp(claimed, db_value)
            if verdict is _VERDICT_OK:
                n_ok += 1
            elif verdict is _VERDICT_BAD:
                n_bad += 1
            else:
                n_uncertain += 1
            suffix = f"（{note}）" if note else ""
            rows.append(f"| {item['claim']} | {claimed} | {db_text or '—'} | {verdict}{suffix} |")

        lines = [
            f"# {title}",
            "",
            "## 验真结论",
            "",
            (
                f"核查 {len(items)} 项：{_VERDICT_OK} {n_ok} 项 | {_VERDICT_BAD} {n_bad} 项"
                f" | {_VERDICT_UNCERTAIN} {n_uncertain} 项"
            ),
            "",
            summary,
            "",
            "## 对账明细",
            "",
            "| 核查项 | 报告声称 | 数据库真值 | 结论 |",
            "|---|---|---|---|",
            *rows,
            "",
            "*本验真报告由 Sluice fact-check Skill 生成，属实/不符判定为确定性代码盖章，非模型判断*",
        ]
        return ToolResult("\n".join(lines))

    @staticmethod
    def _stamp(claimed: str, db_value: Any) -> tuple[str, str]:
        """确定性判定单条结论。返回 (verdict, 真值展示文本)。"""
        try:
            value = float(str(db_value).strip())
        except (TypeError, ValueError):
            return _VERDICT_UNCERTAIN, str(db_value) if db_value not in (None, "") else ""
        verdict = _VERDICT_OK if check_value_in_answer(value, claimed) else _VERDICT_BAD
        return verdict, str(db_value)


TOOLS: list = [FactCheckQueryTool(), RenderVerdictTool()]
