"""Skill 包协议与加载器（P1 最小面）。

Skill 包 = 子目录 + SKILL.md（frontmatter: name/description）+ 可选 tools.py（模块级 TOOLS 列表）。
私有工具以 {skill_name}.{tool_name} 前缀注册，模型视角仍在同一个扁平工具池。
skill.py 钩子与 evals/ 由 P2/P3 消费。
"""

from __future__ import annotations

import importlib.util
import itertools
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..tools.base import Tool
from ..tools.registry import ToolRegistry

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)


def parse_skill_md(text: str) -> dict[str, str]:
    """解析 SKILL.md 顶部 YAML frontmatter（平铺 key: value）。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta


@dataclass
class SkillPackage:
    name: str
    description: str
    path: Path
    tools: list[Tool] = field(default_factory=list)

    def prompt_line(self) -> str:
        return f"- {self.name}: {self.description}"


_MODULE_SEQ = itertools.count()


def _load_tools(py_path: Path, skill_name: str) -> list[Tool]:
    # 模块名加唯一后缀，避免 sys.modules 同名条目被二次 exec 覆盖造成状态串扰
    spec = importlib.util.spec_from_file_location(
        f"sluice_skill_{skill_name}_tools_{next(_MODULE_SEQ)}", py_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return list(getattr(module, "TOOLS", []))


class SkillLoader:
    def load(
        self, skills_dir: str | Path, registry: ToolRegistry | None = None
    ) -> list[SkillPackage]:
        root = Path(skills_dir)
        if not root.is_dir():
            return []
        packages: list[SkillPackage] = []
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            md = d / "SKILL.md"
            if not md.is_file():
                continue
            meta = parse_skill_md(md.read_text(encoding="utf-8"))
            if not meta.get("name"):
                continue
            tools = (
                _load_tools(d / "tools.py", meta["name"]) if (d / "tools.py").is_file() else []
            )
            for t in tools:
                t.name = f"{meta['name']}.{t.name}"
                if registry is not None:
                    registry.register(t)
            packages.append(
                SkillPackage(
                    name=meta["name"],
                    description=meta.get("description", ""),
                    path=d,
                    tools=tools,
                )
            )
        return packages
