"""Milvus 后端测试：鸭子类型 client 注入（协议级），证明换后端不改业务代码。"""

import importlib.util

import pytest

from sluice.exceptions import SluiceError
from sluice.memory.milvus_store import MilvusStore
from sluice.memory.store import HashEmbedder

real_find_spec = importlib.util.find_spec


class FakeMilvusClient:
    """MilvusClient 窄协议替身：create_collection/insert/search。"""

    def __init__(self):
        self.rows: list[dict] = []
        self.created: list[str] = []
        self.dimension = 0

    def has_collection(self, name: str) -> bool:
        return name in self.created

    def create_collection(self, name: str, dimension: int, metric_type: str = "IP", id_type: str = "int"):
        self.created.append(name)
        self.dimension = dimension
        self.id_type = id_type

    def insert(self, collection: str, rows: list[dict]) -> None:
        self.rows.extend(rows)

    def search(self, collection: str, data: list, limit: int, output_fields: list[str]):
        # 简化：暴力扫全部行按内积排序（向量已归一化）
        q = data[0]
        scored = sorted(
            (
                (sum(a * b for a, b in zip(q, r["vector"])), r)
                for r in self.rows
                if r["collection"] == collection
            ),
            key=lambda x: -x[0],
        )[:limit]
        return [[{"entity": {"text": r["text"], "meta_json": r["meta_json"]}, "distance": s} for s, r in scored]]


def test_milvus_store_same_interface_as_faiss(tmp_path):
    """与 FaissStore 同一批断言（接口一致性核心证明）。"""
    from sluice.memory.store import FaissStore

    embed = HashEmbedder(dim=64)
    fake = FakeMilvusClient()
    milvus = MilvusStore(collection="t", dim=64, embed=embed, client=fake)
    faiss = FaissStore(embed=embed, dim=64, path=str(tmp_path / "v"))
    texts = ["上周销售额环比上升", "杭州连续三周居首", "信贷通过率下降"]
    metas = [{"src": "a"}, {"src": "b"}, {"src": "c"}]
    milvus.upsert(texts, metas)
    faiss.upsert(texts, metas)
    # 中文无空格 → HashEmbedder 整句单 token，词面相似无法跨句命中（T10 同款已知限制），
    # 查询用与第一条完全相同的文本保证确定性 top1。
    for store in (milvus, faiss):
        hits = store.search("上周销售额环比上升", k=2)
        assert len(hits) == 2 and hits[0].meta == {"src": "a"} and hits[0].score >= hits[1].score


def test_milvus_store_creates_collection_once():
    fake = FakeMilvusClient()
    MilvusStore(collection="t", dim=64, embed=HashEmbedder(64), client=fake)
    MilvusStore(collection="t", dim=64, embed=HashEmbedder(64), client=fake)
    assert fake.created == ["t"]  # 第二次不重复建表
    assert fake.id_type == "string"  # 字符串主键与 upsert 的 uuid4 主键匹配（真客户端 int 默认会类型不匹配）


def test_milvus_store_missing_pymilvus_raises_clean(monkeypatch):
    """client 未注入且 pymilvus 未安装 → 明确报错（不裸 ImportError）。"""
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "pymilvus" else real_find_spec(name))
    with pytest.raises(SluiceError, match="pymilvus"):
        MilvusStore(collection="t", dim=64, embed=HashEmbedder(64), client=None)
