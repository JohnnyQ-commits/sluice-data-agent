"""本地目录挂载端点：校验 / 复制 / 热注册 / 卸载 / 内置保护。"""

import json
import textwrap

import pytest

from sluice.llm.types import LLMResponse, TextBlock
from sluice.runtime.loop import ReActRuntime
from sluice.tools.registry import ToolRegistry
from sluice.web.app import create_app

BUILTIN_NAME = "demo-builtin"

ECHO_CLIENT_PY = textwrap.dedent(
    """\
    from sluice.tools.base import Tool, ToolResult


    class EchoTool(Tool):
        name = "echo"
        description = "回显输入"
        parameters = {"type": "object", "properties": {}}

        def execute(self, args):
            return ToolResult("echo")


    TOOLS = [EchoTool()]
"""
)


class EchoClient:
    def chat(self, messages, system=None, tools=None):
        return LLMResponse(content=[TextBlock("完成")], stop_reason="end_turn", usage={})


def _builtin_skill(root, name=BUILTIN_NAME):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 内置演示\noutput_contract: 结论\n---\n# t\n",
        encoding="utf-8",
    )
    return d


def _mounted_skill(src_root, name="my-skill", tools_py=ECHO_CLIENT_PY, md_name=None):
    d = src_root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {md_name or name}\ndescription: 用户挂载演示\n---\n# t\n",
        encoding="utf-8",
    )
    if tools_py is not None:
        (d / "tools.py").write_text(tools_py, encoding="utf-8")
    return d


@pytest.fixture
def env(tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _builtin_skill(skills_root)
    src_root = tmp_path / "src"
    src_root.mkdir()
    registry = ToolRegistry()
    app = create_app(
        runtime=ReActRuntime(EchoClient(), registry),
        registry=registry,
        skills_dir=str(skills_root),
    )
    app.config["TESTING"] = True
    return app, skills_root, src_root


def _client(env):
    return env[0].test_client()


def test_get_skills_lists_contract_tools_evals(env):
    resp = _client(env).get("/api/skills")
    skills = resp.get_json()["skills"]
    demo = next(s for s in skills if s["name"] == BUILTIN_NAME)
    assert demo["description"] == "内置演示"
    assert demo["output_contract"] == ["结论"]
    assert demo["evals"] == 0
    assert demo["user_mounted"] is False
    assert set(demo) == {"name", "description", "output_contract", "tools", "evals", "user_mounted"}


def test_mount_clean_skill_roundtrip(env):
    app, skills_root, src_root = env
    _mounted_skill(src_root)
    resp = _client(env).post("/api/skills/mount", json={"path": str(src_root / "my-skill")})
    assert resp.status_code == 200
    # 目录复制进 skills 根，带来源标注
    dest = skills_root / "my-skill"
    assert dest.is_dir()
    meta = json.loads((dest / "_mounted.json").read_text(encoding="utf-8"))
    assert meta["source"].endswith("my-skill")
    # 工具热注册进运行时持有的 registry（skill 私有工具带前缀）
    assert app.config["RUNTIME_REGISTRY"].get("my-skill.echo") is not None
    # 列表可见且标记 user_mounted
    skill = next(
        s for s in _client(env).get("/api/skills").get_json()["skills"] if s["name"] == "my-skill"
    )
    assert skill["user_mounted"] is True
    assert skill["tools"][0]["name"] == "my-skill.echo"


def test_mount_then_unload_roundtrip(env):
    app, skills_root, src_root = env
    _mounted_skill(src_root)
    assert (
        _client(env)
        .post("/api/skills/mount", json={"path": str(src_root / "my-skill")})
        .status_code
        == 200
    )
    resp = _client(env).delete("/api/skills/my-skill")
    assert resp.status_code == 200
    assert not (skills_root / "my-skill").exists()
    assert app.config["RUNTIME_REGISTRY"].get("my-skill.echo") is None
    assert all(
        s["name"] != "my-skill" for s in _client(env).get("/api/skills").get_json()["skills"]
    )


def test_mount_rejects_duplicate_name(env):
    _, _, src_root = env
    _mounted_skill(src_root)
    client = _client(env)
    assert (
        client.post("/api/skills/mount", json={"path": str(src_root / "my-skill")}).status_code
        == 200
    )
    resp = client.post("/api/skills/mount", json={"path": str(src_root / "my-skill")})
    assert resp.status_code == 400
    assert "重名" in resp.get_json()["error"] or "已存在" in resp.get_json()["error"]


def test_mount_rejects_name_mismatch(env):
    _, _, src_root = env
    _mounted_skill(src_root, md_name="other-name")
    resp = _client(env).post("/api/skills/mount", json={"path": str(src_root / "my-skill")})
    assert resp.status_code == 400
    assert "目录名" in resp.get_json()["error"]


def test_mount_rejects_missing_skill_md(env):
    _, _, src_root = env
    d = src_root / "no-md"
    d.mkdir()
    resp = _client(env).post("/api/skills/mount", json={"path": str(d)})
    assert resp.status_code == 400


def test_mount_rejects_missing_dir_and_bad_name(env):
    client = _client(env)
    assert (
        client.post("/api/skills/mount", json={"path": "Z:/definitely/missing"}).status_code == 400
    )
    assert client.post("/api/skills/mount", json={"path": "../escape"}).status_code == 400
    assert client.post("/api/skills/mount", json={}).status_code == 400


def test_mount_rejects_dangerous_tools_py_with_reason(env):
    """安全校验违规原样可见——这是卖点不是报错噪音。"""
    _, _, src_root = env
    tools_py = ECHO_CLIENT_PY + "\nimport subprocess\n"
    _mounted_skill(src_root, tools_py=tools_py)
    resp = _client(env).post("/api/skills/mount", json={"path": str(src_root / "my-skill")})
    assert resp.status_code == 400
    assert "subprocess" in resp.get_json()["error"]
    assert not (env[1] / "my-skill").exists()  # 拒绝时不落任何文件


def test_delete_builtin_skill_403(env):
    resp = _client(env).delete(f"/api/skills/{BUILTIN_NAME}")
    assert resp.status_code == 403
    assert env[1].joinpath(BUILTIN_NAME).is_dir()  # 内置目录原封不动


def test_delete_unknown_skill_404(env):
    assert _client(env).delete("/api/skills/nope").status_code == 404


def test_mount_survives_app_reload(tmp_path):
    """复制的目录持久在 skills 根——重启应用（新建 create_app）照常加载。"""
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _builtin_skill(skills_root)
    src_root = tmp_path / "src"
    src_root.mkdir()
    _mounted_skill(src_root)
    registry = ToolRegistry()
    app = create_app(
        runtime=ReActRuntime(EchoClient(), registry), registry=registry, skills_dir=str(skills_root)
    )
    client = app.test_client()
    assert (
        client.post("/api/skills/mount", json={"path": str(src_root / "my-skill")}).status_code
        == 200
    )
    # 模拟重启：同 skills 根新建 app，挂载的 skill 自动加载
    registry2 = ToolRegistry()
    app2 = create_app(
        runtime=ReActRuntime(EchoClient(), registry2),
        registry=registry2,
        skills_dir=str(skills_root),
    )
    names = [s["name"] for s in app2.test_client().get("/api/skills").get_json()["skills"]]
    assert "my-skill" in names
    assert registry2.get("my-skill.echo") is not None
