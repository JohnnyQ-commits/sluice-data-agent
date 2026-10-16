"""数据周报 Skill 私有工具。"""

from __future__ import annotations

from typing import Any, ClassVar

from sluice.tools.base import Tool, ToolResult


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

        lines = [f"# {title}", "", f"**统计周期**：{period}", "", "## 核心指标", ""]
        for m in metrics:
            delta = m.get("delta")
            lines.append(
                f"- **{m['name']}**：{m['value']}" + (f"（环比 {delta}）" if delta else "")
            )
        highlights = args.get("highlights") or []
        if highlights:
            lines += ["", "## 重点发现", ""]
            lines += [f"- {h}" for h in highlights]
        lines += ["", "*本报告由 Sluice weekly-report Skill 生成*"]
        return ToolResult("\n".join(lines))


TOOLS: list = [RenderReportTool()]
