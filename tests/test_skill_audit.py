"""tools.py 静态安全审计（ast 扫描）：转公开前的门面，每个危险模式至少一条用例。"""

import textwrap

from sluice.web.skill_audit import audit_tools_py


def write(tmp_path, code: str):
    p = tmp_path / "tools.py"
    p.write_text(textwrap.dedent(code), encoding="utf-8")
    return p


def test_clean_tools_py_passes(tmp_path):
    p = write(
        tmp_path,
        """\
        import json
        from pathlib import Path

        class T:
            name = "t"

            def run(self):
                data = json.loads(Path("data.txt").read_text())
                return data
        """,
    )
    assert audit_tools_py(p, tmp_path) == []


def test_import_subprocess_rejected(tmp_path):
    p = write(tmp_path, "import subprocess\n")
    assert any("subprocess" in v for v in audit_tools_py(p, tmp_path))


def test_import_socket_rejected(tmp_path):
    p = write(tmp_path, "import socket\n")
    assert any("socket" in v for v in audit_tools_py(p, tmp_path))


def test_import_ctypes_rejected(tmp_path):
    p = write(tmp_path, "import ctypes\n")
    assert any("ctypes" in v for v in audit_tools_py(p, tmp_path))


def test_from_import_network_lib_rejected(tmp_path):
    for lib in ("httpx", "requests", "urllib.request"):
        p = write(tmp_path, f"from {lib} import get\n")
        assert any(lib.split(".")[0] in v for v in audit_tools_py(p, tmp_path)), lib


def test_os_system_call_rejected(tmp_path):
    p = write(tmp_path, "import os\n\ndef f():\n    os.system('rm -rf /')\n")
    vs = audit_tools_py(p, tmp_path)
    assert any("os.system" in v for v in vs)


def test_eval_and_exec_rejected(tmp_path):
    p = write(tmp_path, "def f(x):\n    return eval(x), exec(x)\n")
    vs = audit_tools_py(p, tmp_path)
    assert any("eval" in v for v in vs)
    assert any("exec" in v for v in vs)


def test_dunder_import_rejected(tmp_path):
    p = write(tmp_path, "def f(n):\n    return __import__(n)\n")
    assert any("__import__" in v for v in audit_tools_py(p, tmp_path))


def test_open_outside_skill_dir_rejected(tmp_path):
    p = write(tmp_path, "def f():\n    return open('/etc/passwd').read()\n")
    vs = audit_tools_py(p, tmp_path)
    assert any("open" in v and "skill 目录之外" in v for v in vs)


def test_open_escape_via_relative_rejected(tmp_path):
    p = write(tmp_path, "def f():\n    return open('../../secrets.txt').read()\n")
    vs = audit_tools_py(p, tmp_path)
    assert any("skill 目录之外" in v for v in vs)


def test_open_inside_skill_dir_allowed(tmp_path):
    p = write(tmp_path, 'def f():\n    return open("notes.md").read()\n')
    assert audit_tools_py(p, tmp_path) == []


def test_open_dynamic_path_rejected(tmp_path):
    """非常量路径无法校验 → 拒绝（不猜意图）。"""
    p = write(tmp_path, "def f(name):\n    return open(name).read()\n")
    vs = audit_tools_py(p, tmp_path)
    assert any("open" in v and "非常量" in v for v in vs)


def test_violations_carry_line_numbers(tmp_path):
    p = write(tmp_path, "import json\nimport socket\n")
    vs = audit_tools_py(p, tmp_path)
    assert any("第 2 行" in v for v in vs)


def test_syntax_error_reported(tmp_path):
    p = write(tmp_path, "def broken(:\n")
    vs = audit_tools_py(p, tmp_path)
    assert len(vs) == 1
    assert "语法" in vs[0]
