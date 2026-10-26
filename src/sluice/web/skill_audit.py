"""tools.py 静态安全审计——Skill 挂载的安全门面（ast 扫描，零执行）。

拦截清单：
- 进程/网络/底层模块 import：subprocess / socket / ctypes / httpx / requests / urllib
- 危险内建调用：eval / exec / __import__ / os.system
- open()：常量路径必须在 skill 目录之内；非常量路径无法校验，直接拒绝
返回违规清单（"第 N 行: 说明"），空列表 = 通过。不执行任何被检代码。
"""

from __future__ import annotations

import ast
from pathlib import Path

# import 即拒绝的模块（含 from x import y 的顶层名）
BANNED_MODULES = ("subprocess", "socket", "ctypes", "httpx", "requests", "urllib")
BANNED_CALLS = ("eval", "exec", "__import__")  # 内建危险调用
_NET_MSG = "禁止网络/进程/底层模块 import"


def _qualname(node: ast.expr) -> str:
    """调用目标的点分名（os.system / open / eval），解析不了的返回空串。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _qualname(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _open_path_literal(call: ast.Call) -> str | None:
    """open() 首参为字符串常量时返回其值，否则 None。"""
    if not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _is_inside(child: str, parent: str) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True


def audit_tools_py(py_path, skill_dir) -> list[str]:
    """返回违规清单；空列表 = 通过。"""
    violations: list[str] = []
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8-sig"))
    except SyntaxError as exc:
        return [f"tools.py 语法错误，无法审计：第 {exc.lineno} 行 {exc.msg}"]

    for node in ast.walk(tree):
        # ---- import / from-import ----
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in BANNED_MODULES:
                    violations.append(f"第 {node.lineno} 行: import {alias.name}（{_NET_MSG}）")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BANNED_MODULES:
                violations.append(f"第 {node.lineno} 行: from {node.module} import …（{_NET_MSG}）")

        # ---- 危险调用 ----
        elif isinstance(node, ast.Call):
            name = _qualname(node.func)
            if name in BANNED_CALLS:
                violations.append(f"第 {node.lineno} 行: {name}() 调用（禁止动态求值/动态 import）")
            elif name == "os.system":
                violations.append(f"第 {node.lineno} 行: os.system() 调用（禁止执行系统命令）")
            elif name == "open":
                literal = _open_path_literal(node)
                if literal is None:
                    violations.append(
                        f"第 {node.lineno} 行: open() 使用非常量路径，无法静态校验，拒绝"
                    )
                else:
                    target = Path(literal)
                    if not target.is_absolute():
                        target = (py_path.parent / target).resolve()
                    if not _is_inside(target, Path(skill_dir)):
                        violations.append(
                            f'第 {node.lineno} 行: open("{literal}") 指向 skill 目录之外，拒绝'
                        )

    return violations
