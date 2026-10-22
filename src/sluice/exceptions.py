class SluiceError(Exception):
    """Sluice 所有自定义异常的基类。"""


class LLMTimeoutError(SluiceError):
    """LLM 请求超时（重试耗尽后）。"""


class LLMConnectionError(SluiceError):
    """LLM 网关连接失败（DNS/网络/拒绝连接等，重试耗尽后）。"""


class LLMHTTPError(SluiceError):
    """LLM 网关返回非 2xx。"""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"LLM HTTP {status_code}: {body[:200]}")


class RoundsExhaustedError(SluiceError):
    """ReAct 循环超过轮次上限仍未产出最终回答。"""


class BudgetExceededError(SluiceError):
    """ReAct 循环超出 token 预算（终止级门禁）。"""


class MCPError(SluiceError):
    """MCP 协议/工具调用错误。

    捕获时 MCPTimeoutError/MCPTransportError 必须置于 except MCPError 之前。
    """


class MCPTransportError(MCPError):
    """MCP 子进程传输层错误（进程退出/管道断裂）。"""


class MCPTimeoutError(MCPError):
    """MCP 请求超时。"""
