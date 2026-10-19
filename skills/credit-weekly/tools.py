"""credit-weekly 私有工具：只读查询信贷表，表级权限收窄（越权查 sales 会被输入门禁拦截）。"""

from sluice.config.settings import Settings
from sluice.tools.query_data import QueryDataTool


class QueryCreditTool(QueryDataTool):
    name = "query_credit"
    description = (
        "只读查询信贷申请表 credit_applications（SQLite，参数 sql 必须为 SELECT/WITH 单条语句）。"
    )

    def table_allowlist(self):
        return ["credit_applications"]


TOOLS = [QueryCreditTool(Settings().demo_db_path)]
