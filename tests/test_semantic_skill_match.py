"""Skill 语义召回测试：超过阈值才启用，召回结果只影响 system 提示（工具池不动）。"""

from pathlib import Path

from sluice.memory.hooks import build_skill_store, select_skills
from sluice.memory.store import FaissStore, HashEmbedder
from sluice.skills.loader import SkillPackage


def _packages(n):
    # description 里 "主题i" 必须是空格切分后的整 token——HashEmbedder 按空格分桶，
    # 中文无空格时 "描述：主题7" 会连成一个 token，查询侧的 "主题7" 无法命中
    return [
        SkillPackage(
            name=f"s{i}",
            description=f"skill {i} 主题{i} 的说明",
            path=Path("."),
            output_contract=[],
        )
        for i in range(n)
    ]


def _store_with(packages):
    store = FaissStore(embed=HashEmbedder(dim=64), dim=64, path=None)
    build_skill_store(store, packages)
    return store


def test_select_skills_disabled_below_threshold():
    packages = _packages(3)
    store = _store_with(packages)
    # 少量 skill → None（调用方维持全量注入，现状行为零变化）
    assert select_skills(packages, "主题1 的请求", store, threshold=5) is None


def test_select_skills_recalls_topk():
    packages = _packages(10)
    store = _store_with(packages)
    picked = select_skills(packages, "主题7 的请求", store, threshold=5, k=3)
    assert picked is not None and len(picked) == 3
    # 查询 token "主题7" 与 s7 的 description 整 token 精确重合 → s7 必进 top-3
    assert any(p.name == "s7" for p in picked)
