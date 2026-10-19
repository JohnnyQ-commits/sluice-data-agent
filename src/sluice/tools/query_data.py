"""内置工具 query_data——demo SQLite 库只读查询，P1 数据周报的数据通道。"""

from __future__ import annotations

import itertools
import re
import sqlite3
from typing import Any, ClassVar

from .base import Tool, ToolResult

_MAX_ROWS = 50
_DML_TOKENS = {"insert", "update", "delete", "drop", "alter", "create"}


def _contains_dml(sql: str) -> bool:
    """WITH 语句正文的 DML 关键字校验。replace() 是合法读函数，只在 replace into 大词组时拒绝。"""
    tokens = re.findall(r"[a-z_]+", sql.lower())
    if any(t in _DML_TOKENS for t in tokens):
        return True
    return any(a == "replace" and b == "into" for a, b in itertools.pairwise(tokens))


class QueryDataTool(Tool):
    name: ClassVar[str] = "query_data"
    description: ClassVar[str] = (
        "在 SQLite 数据库上执行只读 SQL（仅 SELECT/WITH），结果以 markdown 表格返回，最多 50 行。"
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "要执行的只读 SQL 查询"},
        },
        "required": ["sql"],
    }

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._tables: list[str] | None = None

    def table_allowlist(self) -> list[str]:
        """供输入门禁做幻觉表校验：sqlite_master 在册表名，惰性查询并缓存。"""
        if self._tables is None:
            conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
            try:
                rows = conn.execute("select name from sqlite_master where type='table'").fetchall()
            finally:
                conn.close()
            self._tables = [r[0] for r in rows]
        return self._tables

    def execute(self, args: dict[str, Any]) -> ToolResult:
        sql = (args.get("sql") or "").strip()
        first = sql.split(None, 1)[0].lower() if sql else ""
        if first not in ("select", "with"):
            return ToolResult("query_data 仅接受只读查询（SELECT/WITH 开头）", is_error=True)
        if first == "with" and _contains_dml(sql):
            return ToolResult(
                "query_data 仅接受只读查询（WITH 语句正文含 DML 关键字）", is_error=True
            )
        body = sql.rstrip().rstrip(";").rstrip()
        if ";" in body:
            return ToolResult("query_data 仅接受单条 SQL 语句", is_error=True)
        try:
            conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
            try:
                cur = conn.execute(sql)
                rows = cur.fetchmany(_MAX_ROWS + 1)
                cols = [d[0] for d in cur.description] if cur.description else []
            finally:
                conn.close()
        except sqlite3.Error as exc:
            return ToolResult(f"SQL 执行失败: {exc}", is_error=True)

        truncated = len(rows) > _MAX_ROWS
        rows = rows[:_MAX_ROWS]
        lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        lines += [
            "| " + " | ".join("" if v is None else str(v).replace("|", "\\|") for v in r) + " |"
            for r in rows
        ]
        if truncated:
            lines[-1] += f"（仅展示前 {_MAX_ROWS} 行）"
        return ToolResult("\n".join(lines))
