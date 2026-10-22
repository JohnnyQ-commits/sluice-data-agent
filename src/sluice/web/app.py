"""Flask 壳——SSE 转发 + 静态托管，全部逻辑在框架层（设计文档 §7）。"""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from ..config.settings import Settings, resolve_audit_dir
from ..guardrails.chain import GuardrailChain
from ..llm.client import LLMClient
from ..llm.types import Message, TextBlock
from ..memory.hooks import SessionMemory, build_skill_store, select_skills
from ..memory.session import SessionStore
from ..memory.store import FaissStore, GatewayEmbedder, HashEmbedder
from ..runtime.loop import ReActRuntime
from ..runtime.prompt import build_system
from ..skills.loader import SkillLoader
from ..tools.query_data import QueryDataTool
from ..tools.registry import ToolRegistry

_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "web" / "frontend" / "dist"

# web 壳无账号体系：单机固定用户 + 单滚动会话（跨消息累积轮次，超限才摘要升维）
_MEMORY_USER_ID = "local"
_MEMORY_SESSION_ID = "local"

logger = logging.getLogger(__name__)


def _stream_idle_timeout(settings: Settings) -> float:
    """SSE 空闲超时：盖过 LLM 客户端重试链的最坏静默（(重试数+1)×超时 + 余量），不误杀健康长轮次。"""
    return (settings.llm_max_retries + 1) * settings.llm_timeout_seconds + 60


def create_app(client=None, registry=None, runtime=None, skills_dir="skills") -> Flask:
    app = Flask(__name__, static_folder=None)
    settings = Settings()
    settings = resolve_audit_dir(settings)
    client = client or LLMClient(settings)
    if registry is None:
        registry = ToolRegistry()
        registry.register(QueryDataTool(settings.demo_db_path))
    packages = SkillLoader().load(skills_dir, registry) if skills_dir else []
    app.config["SLUICE_SKILLS"] = packages  # CLI 横幅等外部取清单用，避免二次 load

    # MCP 可选接线（默认关：mcp_enabled=False 时行为零变化）
    if settings.mcp_enabled:
        from ..mcp.registry import load_mcp_tools

        mcp_config = Path(__file__).resolve().parents[3] / settings.mcp_config_path
        if mcp_config.is_file():
            import atexit

            mcp_servers = load_mcp_tools(mcp_config)
            for server in mcp_servers:
                for t in server.tools:
                    try:
                        registry.register(t)
                    except ValueError:
                        logger.warning("MCP 工具重名跳过: %s", t.name)
            atexit.register(lambda: [s.close() for s in mcp_servers])

    guardrails = GuardrailChain(settings, packages) if settings.guardrails_enabled else None

    # 记忆可选接线（默认关：memory_enabled=False 时行为零变化）；初始化失败降级为无记忆
    memory = None
    vectors = None
    if settings.memory_enabled:
        try:
            base = Path(__file__).resolve().parents[3] / settings.memory_base_dir
            embed = (
                GatewayEmbedder(
                    settings.llm_base_url, settings.llm_api_key, settings.memory_embed_model
                )
                if settings.memory_embed_model
                else HashEmbedder(dim=64)
            )
            vectors = FaissStore(embed, dim=64, path=str(base / "vectors"))
            # skill 索引只建一次：摘要升维后 len(vectors)>0，重启时不能拿条数判断
            built_marker = base / "skill_index.built"
            if packages and not built_marker.exists():
                build_skill_store(vectors, packages)
                built_marker.parent.mkdir(parents=True, exist_ok=True)
                built_marker.touch()
            elif built_marker.exists():
                logger.info("skill 索引已存在，跳过重建（删除 %s 可强制）", built_marker)

            def _summarize(text: str) -> str:
                resp = client.chat(
                    [Message("user", [TextBlock(f"用三句话总结以下对话：\n{text}")])]
                )
                return resp.text

            memory = SessionMemory(
                sessions=SessionStore(base / "sessions"),
                vectors=vectors,
                summarize=_summarize,
                max_turns=settings.memory_max_turns,
            )
        except Exception as exc:  # noqa: BLE001 — 记忆挂了不拖垮对话，kill switch 降级
            logger.warning("记忆层初始化失败，本次启动降级为无记忆: %s", exc)
            memory = None
            vectors = None

    def _memory_system(message: str) -> str | None:
        """memory_enabled 时按消息重算 system（语义召回 skill 子集 + 长期记忆召回）；None = 用 runtime 默认。"""
        if memory is None or vectors is None:
            return None
        try:
            system = build_system(packages)
            picked = select_skills(
                packages, message, vectors, settings.memory_skill_match_threshold
            )
            if picked is not None:
                system = build_system(picked)
            recalled = memory.recall(_MEMORY_USER_ID, _MEMORY_SESSION_ID, message)
            if recalled:
                system += "\n\n[长期记忆召回]\n" + recalled
            return system
        except Exception as exc:  # noqa: BLE001 — 召回失败本轮降级为默认 system，不拖垮对话
            logger.warning("记忆召回失败，本轮降级为默认 system: %s", exc)
            return None

    runtime = runtime or ReActRuntime(
        client,
        registry,
        system=build_system(packages),
        max_rounds=settings.react_max_rounds,
        guardrails=guardrails,
    )

    runs: dict[str, queue.Queue] = {}

    @app.post("/api/chat")
    def chat():
        body = request.get_json(silent=True) or {}
        if not body.get("message"):
            return jsonify({"error": "message 必填"}), 400
        run_id = uuid.uuid4().hex
        q: queue.Queue = queue.Queue()
        runs[run_id] = q

        def worker():
            try:
                # runtime 会 emit final / error 事件，worker 不重复发
                answer = runtime.run(
                    body["message"], on_event=q.put, system=_memory_system(body["message"])
                )
            except Exception:  # noqa: BLE001 — error 事件已由 runtime 发出，这里仅收尾防线程裸死
                return
            if memory is not None:
                try:
                    memory.record(_MEMORY_USER_ID, _MEMORY_SESSION_ID, "user", body["message"])
                    memory.record(_MEMORY_USER_ID, _MEMORY_SESSION_ID, "assistant", answer)
                except Exception:  # noqa: BLE001 — 记忆落盘失败不拖垮对话
                    logger.warning("会话记忆落盘失败: %s", body["message"][:50])

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"run_id": run_id})

    @app.get("/api/stream/<run_id>")
    def stream(run_id: str):
        q = runs.get(run_id)
        if q is None:
            return jsonify({"error": "未知 run_id"}), 404

        def generate():
            while True:
                try:
                    event = q.get(timeout=_stream_idle_timeout(settings))
                except queue.Empty:
                    runs.pop(run_id, None)
                    yield 'event: end\ndata: {"type": "timeout"}\n\n'
                    return
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") in ("final", "error"):
                    runs.pop(run_id, None)
                    yield "event: end\ndata: {}\n\n"
                    return

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no-cache"},
        )

    @app.get("/api/skills")
    def skills():
        return jsonify(
            {"skills": [{"name": p.name, "description": p.description} for p in packages]}
        )

    @app.get("/")
    def index():
        if (_FRONTEND_DIST / "index.html").is_file():
            return send_from_directory(_FRONTEND_DIST, "index.html")
        return (
            "前端未构建：web/frontend/dist 不存在，先在 web/frontend 下 npm install && npm run build",
            200,
        )

    @app.get("/assets/<path:filename>")
    def assets(filename: str):
        return send_from_directory(_FRONTEND_DIST / "assets", filename)

    return app
