"""最小 MCP stdio server（仅测试用）：initialize / tools/list / tools/call(echo, add)。

以行分帧读 stdin、写 stdout——与 StdioTransport 构成真实子进程端到端（零网络）。
"""

import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "回显输入",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "add",
        "description": "两数相加",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
]


def reply(req_id, result):
    print(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}, ensure_ascii=False), flush=True)


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    method, req_id = req.get("method"), req.get("id")
    if method == "initialize":
        reply(req_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "mini", "version": "0.1"}})
    elif method == "tools/list":
        reply(req_id, {"tools": TOOLS})
    elif method == "tools/call":
        name = req["params"]["name"]
        args = req["params"].get("arguments", {})
        if name == "echo":
            reply(req_id, {"content": [{"type": "text", "text": args.get("text", "")}]})
        elif name == "add":
            reply(req_id, {"content": [{"type": "text", "text": str(args.get("a", 0) + args.get("b", 0))}]})
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"unknown tool {name}"}}, ensure_ascii=False), flush=True)
    # notifications/initialized 等无 id 消息：静默
