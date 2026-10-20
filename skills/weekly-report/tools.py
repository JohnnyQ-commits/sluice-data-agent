"""数据周报 Skill 私有工具。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sluice.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from sluice.tools.query_data import QueryDataTool


class RenderReportTool(Tool):
    name: ClassVar[str] = "render_report"
    description: ClassVar[str] = "把指标渲染成 markdown 数据周报。"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "报告标题"},
            "period": {"type": "string", "description": "统计周期，如 2026-10-13 ~ 2026-10-19"},
            "metrics": {
                "type": "array",
                "description": "核心指标列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "value": {"type": "string"},
                        "delta": {"type": "string", "description": "环比变化，如 +15%"},
                    },
                    "required": ["name", "value"],
                },
            },
            "highlights": {
                "type": "array",
                "items": {"type": "string"},
                "description": "重点发现列表",
            },
        },
        "required": ["title", "period", "metrics"],
    }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        title = args.get("title")
        period = args.get("period")
        metrics = args.get("metrics")
        if not title or not period or not metrics:
            return ToolResult("title/period/metrics 均为必填", is_error=True)
        if not isinstance(metrics, list):
            return ToolResult("metrics 必须是数组", is_error=True)
        for m in metrics:
            if not isinstance(m, dict) or "name" not in m or "value" not in m:
                return ToolResult("metrics 元素缺少 name/value", is_error=True)

        lines = [f"# {title}", "", f"**统计周期**：{period}", "", "## 核心指标", ""]
        for m in metrics:
            delta = m.get("delta")
            has_delta = delta is not None and delta != ""
            lines.append(
                f"- **{m['name']}**：{m['value']}" + (f"（环比 {delta}）" if has_delta else "")
            )
        highlights = args.get("highlights")
        if highlights is not None and not isinstance(highlights, list):
            return ToolResult("highlights 必须是数组", is_error=True)
        if highlights:
            lines += ["", "## 重点发现", ""]
            lines += [f"- {h}" for h in highlights]
        lines += ["", "*本报告由 Sluice weekly-report Skill 生成*"]
        return ToolResult("\n".join(lines))


class QuerySalesTool(Tool):
    """Skill 前缀查询工具：P4 修复 intent_route 结构性 fail——skill 查数不再依赖平台裸 query_data。"""

    name: ClassVar[str] = "query_sales"
    description: ClassVar[str] = (
        "在 demo 库执行只读 SQL（表 sales），返回行文本。查数一律用本工具，不要用平台裸工具。"
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
        """转发内核的表名白名单——幻觉表门禁对前缀工具同样生效，不能哑火。"""
        return self._kernel_tool().table_allowlist()

    def execute(self, args: dict[str, Any]) -> ToolResult:
        sql = args.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return ToolResult("sql 必须是非空字符串", is_error=True)
        return self._kernel_tool().execute({"sql": sql})


TOOLS: list = [RenderReportTool(), QuerySalesTool()]
