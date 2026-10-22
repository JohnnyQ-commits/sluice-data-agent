"""挂起不响应的 MCP server（仅测试用）：收到请求不回话，stdin EOF 时留痕。

供孤儿进程泄漏回归测试使用：load_mcp_tools 握手超时跳过后必须 close（收 stdin），
本进程才会走到 EOF 写 marker——marker 文件出现即证明 close 被调用、泄漏已堵。
"""

import sys
from pathlib import Path

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

marker = Path(sys.argv[1])
for line in sys.stdin:
    pass  # 收到请求不回话——挂起到父端超时
marker.with_suffix(".closed").write_text("closed", encoding="utf-8")
