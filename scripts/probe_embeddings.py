"""网关 /v1/embeddings 探针：确认端点存在性、请求体形状与返回结构。

对应 GatewayEmbedder（src/sluice/memory/store.py）的端点假设（OpenAI 兼容 /v1/embeddings）。
key 从仓库根 .env 手工解析读取，不从进程环境继承；运行前显式清掉 ANTHROPIC_* 环境变量。

用法:
    python scripts/probe_embeddings.py [MODEL]
    MODEL 缺省取 .env 的 SLUICE_MEMORY_EMBED_MODEL，未配置则回落 SLUICE_LLM_MODEL。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 30.0


def _clear_anthropic_env() -> None:
    """探针只信 .env：显式清掉进程里可能残留的 ANTHROPIC_* 变量。"""
    for key in [k for k in os.environ if k.startswith("ANTHROPIC_")]:
        os.environ.pop(key, None)


def _load_env(path: Path) -> dict[str, str]:
    """手解析 .env（不引第三方依赖、不碰进程环境）：KEY=VALUE，# 注释与空行跳过。"""
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main() -> int:
    _clear_anthropic_env()
    env = _load_env(REPO_ROOT / ".env")
    base_url = (env.get("SLUICE_LLM_BASE_URL") or "").rstrip("/")
    api_key = env.get("SLUICE_LLM_API_KEY") or ""
    if not base_url or not api_key:
        print("错误: .env 缺 SLUICE_LLM_BASE_URL / SLUICE_LLM_API_KEY", file=sys.stderr)
        return 2
    model = (
        sys.argv[1]
        if len(sys.argv) > 1
        else env.get("SLUICE_MEMORY_EMBED_MODEL") or env.get("SLUICE_LLM_MODEL") or ""
    )
    if not model:
        print("错误: 未指定模型且 .env 无 SLUICE_MEMORY_EMBED_MODEL / SLUICE_LLM_MODEL", file=sys.stderr)
        return 2

    import httpx

    url = f"{base_url}/v1/embeddings"
    body = {"model": model, "input": ["测试"]}
    print(f"POST {url}")
    print(f"请求体: {json.dumps(body, ensure_ascii=False)}")
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:  # 连接失败/超时等网络层错误（httpx 异常基类）
        print(f"网络层失败: {type(exc).__name__}: {exc}")
        return 1

    print(f"HTTP 状态: {resp.status_code}")
    if resp.status_code >= 400:
        print(f"响应体前 500 字符: {resp.text[:500]}")
        return 1

    payload = resp.json()
    data = payload.get("data") or []
    embedding = data[0].get("embedding") if data else None
    summary = {
        "model": payload.get("model"),
        "object": payload.get("object"),
        "data 条数": len(data),
        "data[0].object": data[0].get("object") if data else None,
        "embedding 维度": len(embedding) if embedding else None,
        "usage": payload.get("usage"),
        "顶层键": sorted(payload.keys()),
    }
    print("返回结构:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
