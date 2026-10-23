"""user-activity 私有工具：只读查询用户活跃事件表，表级权限收窄（越权查 sales 会被输入门禁拦截）。"""

from sluice.config.settings import Settings
from sluice.tools.query_data import QueryDataTool


class QueryEventsTool(QueryDataTool):
    name = "query_events"
    description = (
        "只读查询用户活跃事件表 user_events（SQLite，参数 sql 必须为 SELECT/WITH 单条语句）。"
    )

    def table_allowlist(self):
        return ["user_events"]


TOOLS = [QueryEventsTool(Settings().demo_db_path)]
