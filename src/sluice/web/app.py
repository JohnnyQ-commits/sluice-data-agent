"""Flask 壳——SSE 转发 + 静态托管，全部逻辑在框架层（设计文档 §7）。"""

from __future__ import annotations

import json
import logging
import queue
import re
import shutil
import threading
import uuid
from datetime import datetime
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
from ..skills.loader import SkillLoader, parse_skill_md
from ..tools.query_data import QueryDataTool
from ..tools.registry import ToolRegistry
from ..web.conversations import ConversationStore
from ..web.skill_audit import audit_tools_py

_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "web" / "frontend" / "dist"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKILL_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")  # 目录名 = skill 名，防路径穿越

# web 壳无账号体系：单机固定用户 + 单滚动会话（跨消息累积轮次，超限才摘要升维）
_MEMORY_USER_ID = "local"
_MEMORY_SESSION_ID = "local"

logger = logging.getLogger(__name__)


def _stream_idle_timeout(settings: Settings) -> float:
    """SSE 空闲超时：盖过 LLM 客户端重试链的最坏静默（(重试数+1)×超时 + 余量），不误杀健康长轮次。"""
    return (settings.llm_max_retries + 1) * settings.llm_timeout_seconds + 60


def create_app(
    client=None, registry=None, runtime=None, skills_dir="skills", conversations_dir=None
) -> Flask:
    app = Flask(__name__, static_folder=None)
    settings = Settings()
    settings = resolve_audit_dir(settings)
    # 会话持久化：默认 repo 根 data/conversations/（与 audit/ 同级），测试可注入 tmp_path
    conversations = ConversationStore(
        conversations_dir
        if conversations_dir is not None
        else _REPO_ROOT / "data" / "conversations"
    )
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

    def _memory_system(message: str, skill_name: str | None = None) -> str | None:
        """按消息重算 system。

        skill_name 指定时跳过语义匹配，直接注入该 skill 的 system（长期记忆召回仍叠加；
        未知 skill 名回落默认链路）；缺省时 memory_enabled 走语义召回，None = 用 runtime 默认。
        """
        if skill_name:
            pkg = next((p for p in packages if p.name == skill_name), None)
            if pkg is not None:
                system = build_system([pkg])
                if memory is not None:
                    try:
                        recalled = memory.recall(_MEMORY_USER_ID, _MEMORY_SESSION_ID, message)
                        if recalled:
                            system += "\n\n[长期记忆召回]\n" + recalled
                    except Exception:  # noqa: BLE001 — 召回失败仅用 skill system，不拖垮对话
                        logger.warning("指定 skill 的记忆召回失败，仅注入 skill system")
                return system
            logger.warning("指定 skill 未找到: %s，回落默认链路", skill_name)
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

    # Skill 挂载：热注册目标必须是运行时实际持有的工具池（外部注入 runtime 时保持同一实例）
    app.config["RUNTIME_REGISTRY"] = runtime.registry
    app.config["SLUICE_SKILLS_DIR"] = str(skills_dir) if skills_dir else None

    def _runtime_registry() -> ToolRegistry:
        return runtime.registry

    def _session_init(message: str, skill_name: str | None = None) -> dict:
        """开场事件：本次运行的可观测性快照。

        skills 取 system prompt 实际注入的清单（指定 skill 时为该 skill；memory 召回
        可能筛出子集，与 _memory_system 同源同逻辑）；tools 以 registry 为唯一真相源全量遍历。
        """
        forced = skill_name if any(p.name == skill_name for p in packages) else None
        if forced:
            skills = [forced]
        else:
            skills = [p.name for p in packages]
            if memory is not None and vectors is not None:
                try:
                    picked = select_skills(
                        packages, message, vectors, settings.memory_skill_match_threshold
                    )
                    if picked is not None:
                        skills = [p.name for p in picked]
                except Exception:  # noqa: BLE001 — 召回失败按全量报告，不拖垮开场
                    logger.warning("session_init 召回 Skill 子集失败，按全量报告")
        return {
            "type": "session_init",
            "model": settings.llm_model,
            "skill": forced,  # 用户强制指定的 skill；None = 自动匹配
            "skills": skills,
            "tools": [
                {"name": d.name, "description": d.description}
                for d in runtime.registry.to_defs()
            ],
            "guardrails": runtime.guardrails is not None,
            "max_rounds": runtime.max_rounds,
        }

    runs: dict[str, queue.Queue] = {}

    @app.post("/api/chat")
    def chat():
        body = request.get_json(silent=True) or {}
        if not body.get("message"):
            return jsonify({"error": "message 必填"}), 400
        run_id = uuid.uuid4().hex
        q: queue.Queue = queue.Queue()
        persisted = threading.Event()  # worker 落盘完成信号（stream 终结前等待）
        runs[run_id] = (q, persisted)

        # 会话归属：无 id 或 id 失效 → 新建并在响应中告知前端
        conversation_id = body.get("conversation_id")
        if not isinstance(conversation_id, str) or conversations.load(conversation_id) is None:
            conversation_id = conversations.create()

        def worker():
            events = []
            answer = None

            def tee(ev):
                events.append(ev)
                q.put(ev)

            try:
                # 指定 skill（可选）→ 跳过语义匹配直接注入该 skill system，缺省零变化
                skill_name = (
                    body["skill"] if isinstance(body.get("skill"), str) and body.get("skill") else None
                )
                # 开场先发 session_init（模型/Skill/工具清单/门禁/轮次上限），
                # 经同一 tee 通道进轨迹与落盘，回放时可见；数据取自 web 层已持有的
                # settings/runtime/registry，runtime.run 签名与 CLI 语义零变化
                tee(_session_init(body["message"], skill_name))
                # runtime 会 emit final / error 事件，worker 不重复发
                answer = runtime.run(
                    body["message"],
                    on_event=tee,
                    system=_memory_system(body["message"], skill_name),
                )
            except Exception as exc:  # noqa: BLE001 — error 事件已 tee，这里仅收尾防线程裸死
                logger.warning("本轮运行异常终止（error 事件已发）: %s", exc)
            # 每轮增量落盘（final 不进轨迹，answer 单独存；error 收尾也留痕）
            try:
                conversations.append_turn(
                    conversation_id,
                    body["message"],
                    [e for e in events if e.get("type") != "final"],
                    answer,
                )
            except Exception:  # noqa: BLE001 — 会话写盘失败不拖垮对话
                logger.warning("会话落盘失败: %s", body["message"][:50])
            if memory is not None and answer is not None:
                try:
                    memory.record(_MEMORY_USER_ID, _MEMORY_SESSION_ID, "user", body["message"])
                    memory.record(_MEMORY_USER_ID, _MEMORY_SESSION_ID, "assistant", answer)
                except Exception:  # noqa: BLE001 — 记忆落盘失败不拖垮对话
                    logger.warning("会话记忆落盘失败: %s", body["message"][:50])
            persisted.set()

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"run_id": run_id, "conversation_id": conversation_id})

    # ---------------- 会话持久化端点（前端侧栏） ----------------

    @app.get("/api/conversations")
    def list_conversations():
        q = request.args.get("q", "").strip()
        if q:
            return jsonify({"conversations": conversations.search(q)})
        return jsonify({"conversations": conversations.list()})

    @app.post("/api/conversations")
    def create_conversation():
        return jsonify({"id": conversations.create()})

    @app.get("/api/conversations/<conv_id>")
    def get_conversation(conv_id: str):
        conv = conversations.load(conv_id)
        if conv is None:
            return jsonify({"error": "会话不存在"}), 404
        return jsonify(conv)

    @app.delete("/api/conversations/<conv_id>")
    def delete_conversation(conv_id: str):
        if not conversations.delete(conv_id):
            return jsonify({"error": "会话不存在"}), 404
        return jsonify({"ok": True})

    @app.get("/api/stream/<run_id>")
    def stream(run_id: str):
        entry = runs.get(run_id)
        if entry is None:
            return jsonify({"error": "未知 run_id"}), 404
        q, persisted = entry

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
                    persisted.wait(timeout=10)  # 落盘先于 end，读回一致性
                    yield "event: end\ndata: {}\n\n"
                    return

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no-cache"},
        )

    @app.get("/api/skills")
    def skills():
        def payload(p):
            return {
                "name": p.name,
                "description": p.description,
                "output_contract": p.output_contract,
                "tools": [{"name": t.name, "description": t.description} for t in p.tools],
                "evals": len(p.evals),
                "user_mounted": (p.path / "_mounted.json").is_file(),
            }

        return jsonify({"skills": [payload(p) for p in packages]})

    @app.post("/api/skills/mount")
    def mount_skill():
        body = request.get_json(silent=True) or {}
        raw = (body.get("path") or "").strip()
        if not raw:
            return jsonify({"error": "path 必填（服务器本地绝对路径）"}), 400
        src = Path(raw)
        if not src.is_absolute():
            return jsonify({"error": "path 必须为服务器本地绝对路径"}), 400
        src = src.resolve()
        if not src.is_dir():
            return jsonify({"error": f"目录不存在: {src}"}), 400
        name = src.name
        if not _SKILL_NAME_RE.fullmatch(name):
            return jsonify({"error": f"目录名不合法（仅字母/数字/-/_，防路径穿越）: {name}"}), 400
        md = src / "SKILL.md"
        if not md.is_file():
            return jsonify({"error": "缺少 SKILL.md"}), 400
        meta = parse_skill_md(md.read_text(encoding="utf-8-sig"))
        if not meta.get("name"):
            return jsonify({"error": "SKILL.md frontmatter 缺少 name"}), 400
        if meta["name"] != name:
            return jsonify(
                {"error": f"frontmatter name（{meta['name']}）与目录名（{name}）不一致"}
            ), 400
        if any(p.name == name for p in packages):
            return jsonify({"error": f"已挂载同名 Skill，拒绝重名: {name}"}), 400
        tools_py = src / "tools.py"
        if tools_py.is_file():
            violations = audit_tools_py(tools_py, src)
            if violations:
                return jsonify({"error": "安全校验未通过:\n" + "\n".join(violations)}), 400
        skills_root = app.config.get("SLUICE_SKILLS_DIR")
        if not skills_root:
            return jsonify({"error": "应用未启用 skills 目录，无法挂载"}), 400
        dest = Path(skills_root) / name
        if dest.exists():
            return jsonify({"error": f"skills 目录已存在同名目录: {name}"}), 400
        try:
            shutil.copytree(src, dest)
            (dest / "_mounted.json").write_text(
                json.dumps(
                    {"source": str(src), "mounted_at": datetime.now().isoformat()},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            pkg = SkillLoader().load_package(dest, _runtime_registry())
        except Exception as exc:  # noqa: BLE001 — 复制/加载失败回滚，不留半挂载
            shutil.rmtree(dest, ignore_errors=True)
            return jsonify({"error": f"挂载失败: {exc}"}), 400
        if pkg is None:
            shutil.rmtree(dest, ignore_errors=True)
            return jsonify({"error": "Skill 加载失败（详见服务端日志）"}), 400
        packages.append(pkg)
        return jsonify({"name": pkg.name, "tools": len(pkg.tools)})

    @app.delete("/api/skills/<name>")
    def unload_skill(name: str):
        pkg = next((p for p in packages if p.name == name), None)
        if pkg is None:
            return jsonify({"error": "Skill 不存在"}), 404
        if not (pkg.path / "_mounted.json").is_file():
            return jsonify({"error": f"{name} 为内置 Skill，禁止卸载"}), 403
        _runtime_registry().unregister_owner(name)
        shutil.rmtree(pkg.path, ignore_errors=True)
        packages.remove(pkg)
        return jsonify({"ok": True})

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
