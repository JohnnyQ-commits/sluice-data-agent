"""MCP stdio 传输：子进程生命周期 + 行分帧请求-响应关联。

设计要点：
- 读线程 pump：子进程 stdout → queue（Windows 管道不支持带超时的阻塞读）
- 请求按 id 关联；非匹配响应/通知丢弃（本 client 无服务端主动请求需求）
- 超时 / 进程死亡 / 协议 error 三分类异常
- 编码契约：Popen 两端显式 encoding="utf-8"（Windows locale 编码会炸中文）

线程契约：实例方法线程安全（内部互斥锁），但请求仍串行化——
并发工具调用在调用方层面排队（request 持锁等待响应期间其他 request 阻塞）。
"""

from __future__ import annotations

import itertools
import queue
import subprocess
import threading
import time

from ..exceptions import MCPError, MCPTimeoutError, MCPTransportError
from . import protocol

_MAX_LINE_BYTES = 1 << 20  # 1MB：单行上限，防失控服务器 OOM


class StdioTransport:
    def __init__(self, command: list[str], timeout: float = 30.0, max_restarts: int = 3):
        self._command = command
        self._timeout = timeout
        self._max_restarts = max_restarts
        self._restarts = 0
        self._proc: subprocess.Popen | None = None
        self._queue: queue.Queue[dict | None] = queue.Queue()
        self._ids = itertools.count(1)
        self._lock = threading.Lock()  # 串行化 request/notify/ensure_alive——并发 request 共享 _queue 会互偷响应

    def start(self) -> None:
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",  # 非法字节不炸读线程（合法 UTF-8 含中文不受影响）
            bufsize=1,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        """读线程：stdout 逐行 → queue。捕获 proc/queue 为局部变量——

        ensure_alive 重启后旧线程若还挂着，其 EOF 的 None 只进旧队列，不污染新队列。
        """
        proc, q = self._proc, self._queue
        assert proc and proc.stdout
        try:
            for line in proc.stdout:
                # 限制单行内存上界（整行仍会先入内存；真实 MCP 大响应若超限
                # 会被判流异常，有 ensure_alive 兜底）
                if len(line.encode("utf-8", errors="replace")) > _MAX_LINE_BYTES:
                    q.put(None)  # 超长行：视作流异常
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    q.put(protocol.parse_message(line))
                except MCPError:
                    q.put(None)  # 无法解析 → 视作流终止
                    return
            q.put(None)  # EOF：进程关闭了 stdout
        except Exception:  # noqa: BLE001 — 兜底必须宽于 OSError/解码错，读线程死则 in-flight request 吃满超时
            q.put(None)  # Windows 硬杀后读缓冲可抛 OSError——兜底唤醒 in-flight request

    def _write_line(self, line: str) -> None:
        if self._proc is None or self._proc.poll() is not None:
            raise MCPTransportError(f"MCP 子进程未运行: {self._command}")
        assert self._proc.stdin
        try:
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
        except OSError as exc:  # 写瞬间进程死亡（poll 检查后的窗口）
            raise MCPTransportError(f"MCP 子进程管道断裂: {exc}") from exc

    def request(self, method: str, params: dict) -> dict:
        with self._lock:
            req_id = next(self._ids)
            self._write_line(protocol.encode_request(method, params, req_id))
            deadline = time.time() + self._timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise MCPTimeoutError(f"MCP 请求超时({self._timeout}s): {method}")
                try:
                    msg = self._queue.get(timeout=min(remaining, 0.5))
                except queue.Empty:
                    continue
                if msg is None:
                    raise MCPTransportError("MCP 子进程已退出")
                if msg.get("id") == req_id:
                    if "error" in msg:
                        err = msg["error"]
                        raise MCPError(f"MCP 协议错误 {err.get('code')}: {str(err.get('message', ''))[:200]}")
                    return msg
                # 其他 id 的响应/通知：丢弃

    def notify(self, method: str, params: dict) -> None:
        """JSON-RPC 通知：无 id 无响应，只写不读。"""
        with self._lock:
            self._write_line(protocol.encode_request(method, params, None))

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.close()  # 先收 EOF：很多 stdio server 会自退
            except OSError:
                pass
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()

    def ensure_alive(self) -> bool:
        """进程死亡则重启（计数上限 max_restarts）；True=已重启需重新 initialize。

        _restarts 整个生命周期累计熔断（跨天服务偶发 3 崩溃即永久失效）——
        已知取舍，T8 若做长期驻留再议冷却机制。
        """
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return False
            if self._restarts >= self._max_restarts:
                raise MCPTransportError(f"MCP 子进程重启超上限({self._max_restarts}): {self._command}")
            self._restarts += 1
            self._queue = queue.Queue()  # 换新队列：旧 pump 的 EOF 信号不再可达
            self.start()
            return True
