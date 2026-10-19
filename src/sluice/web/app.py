"""Flask 壳——SSE 转发 + 静态托管，全部逻辑在框架层（设计文档 §7）。"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from ..config.settings import Settings
from ..llm.client import LLMClient
from ..runtime.loop import ReActRuntime
from ..skills.loader import SkillLoader
from ..tools.query_data import QueryDataTool
from ..tools.registry import ToolRegistry

_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "web" / "frontend" / "dist"


def create_app(client=None, registry=None, runtime=None, skills_dir="skills") -> Flask:
    app = Flask(__name__, static_folder=None)
    settings = Settings()
    client = client or LLMClient(settings)
    if registry is None:
        registry = ToolRegistry()
        registry.register(QueryDataTool(settings.demo_db_path))
    packages = SkillLoader().load(skills_dir, registry) if skills_dir else []
    runtime = runtime or ReActRuntime(
        client,
        registry,
        system=_build_system(packages),
        max_rounds=settings.react_max_rounds,
    )

    runs: dict[str, dict] = {}

    @app.post("/api/chat")
    def chat():
        body = request.get_json(silent=True) or {}
        if not body.get("message"):
            return jsonify({"error": "message 必填"}), 400
        run_id = uuid.uuid4().hex
        q: queue.Queue = queue.Queue()
        runs[run_id] = {"queue": q, "done": False}

        def worker():
            try:
                # runtime 会 emit final / error 事件，worker 不重复发
                runtime.run(body["message"], on_event=q.put)
            except Exception:  # noqa: BLE001, S110 — error 事件已由 runtime 发出，这里仅收尾防线程裸死
                pass
            finally:
                runs[run_id]["done"] = True

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"run_id": run_id})

    @app.get("/api/stream/<run_id>")
    def stream(run_id: str):
        run = runs.get(run_id)
        if run is None:
            return jsonify({"error": "未知 run_id"}), 404

        def generate():
            q = run["queue"]
            while True:
                try:
                    event = q.get(timeout=60)
                except queue.Empty:
                    yield 'event: end\ndata: {"type": "timeout"}\n\n'
                    return
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") in ("final", "error"):
                    yield "event: end\ndata: {}\n\n"
                    return

        return Response(generate(), mimetype="text/event-stream")

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


def _build_system(packages) -> str:
    base = "你是 Sluice 平台的助手。可以调用提供的工具完成任务，完成后直接给出最终回答。"
    if packages:
        lines = "\n".join(p.prompt_line() for p in packages)
        base += "\n\n可用的 Skill（通过其带前缀的工具使用）：\n" + lines
    return base
