"""异常下钻定位 Skill 私有工具。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sluice.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from sluice.tools.query_data import QueryDataTool


class TriageQueryTool(Tool):
    """Skill 前缀查询工具：单域 sales，静态 allowlist 与周报 Skill 同规格。"""

    name: ClassVar[str] = "query_data"
    description: ClassVar[str] = (
        "在 demo 库执行只读 SQL（表 sales），返回行文本。下钻查数一律用本工具。"
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
        """静态声明本 Skill 的域内表——不转发内核 sqlite_master 全量名单（备案④同款）。"""
        return ["sales"]

    def execute(self, args: dict[str, Any]) -> ToolResult:
        sql = args.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return ToolResult("sql 必须是非空字符串", is_error=True)
        return self._kernel_tool().execute({"sql": sql})


class RenderTriageTool(Tool):
    """下钻报告渲染：结构校验（结论必须挂证据），渲染交本工具，定位判断留给模型。"""

    name: ClassVar[str] = "render_triage"
    description: ClassVar[str] = (
        "渲染异常下钻报告：下钻结论 + 逐层证据链。每个结论必须携带查库数字佐证。"
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "报告标题"},
            "conclusion": {"type": "string", "description": "下钻结论（定位到维度切片与变动幅度）"},
            "findings": {
                "type": "array",
                "description": "逐层证据链，从整体到嫌疑切片",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {
                            "type": "string",
                            "description": "下钻层级，如 整体/按城市/按日期",
                        },
                        "finding": {"type": "string", "description": "该层发现（含查库数字）"},
                        "evidence": {
                            "type": "string",
                            "description": "SQL 查出的佐证数值（直接引用返回值，禁止心算）",
                        },
                    },
                    "required": ["step", "finding", "evidence"],
                },
            },
        },
        "required": ["title", "conclusion", "findings"],
    }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        title = args.get("title")
        conclusion = args.get("conclusion")
        findings = args.get("findings")
        if not title or not conclusion:
            return ToolResult("title/conclusion 均为必填", is_error=True)
        if not isinstance(findings, list) or not findings:
            return ToolResult("findings 必须是非空数组（证据链不能为空）", is_error=True)

        rows: list[str] = []
        for f in findings:
            if not isinstance(f, dict) or not f.get("step") or not f.get("finding"):
                return ToolResult("findings 元素缺少 step/finding", is_error=True)
            evidence = f.get("evidence")
            # 结论必须挂数字：evidence 抽不到数值 = 无佐证猜测，直接报错打回
            if not isinstance(evidence, str) or not any(ch.isdigit() for ch in evidence):
                return ToolResult(
                    f"层级 [{f['step']}] 缺少数值佐证（evidence 须为 SQL 查出的数字，禁止纯文字结论）",
                    is_error=True,
                )
            rows.append(f"| {f['step']} | {f['finding']} | {evidence} |")

        lines = [
            f"# {title}",
            "",
            "## 下钻结论",
            "",
            conclusion,
            "",
            "## 证据链",
            "",
            "| 下钻层级 | 发现 | 数值佐证 |",
            "|---|---|---|",
            *rows,
            "",
            "*本报告由 Sluice anomaly-triage Skill 生成，结论均有查库数字佐证*",
        ]
        return ToolResult("\n".join(lines))


TOOLS: list = [TriageQueryTool(), RenderTriageTool()]
