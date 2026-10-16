class SluiceError(Exception):
    """Sluice 所有自定义异常的基类。"""


class LLMTimeoutError(SluiceError):
    """LLM 请求超时（重试耗尽后）。"""


class LLMHTTPError(SluiceError):
    """LLM 网关返回非 2xx。"""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"LLM HTTP {status_code}: {body[:200]}")


class RoundsExhaustedError(SluiceError):
    """ReAct 循环超过轮次上限仍未产出最终回答。"""
