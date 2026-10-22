"""Milvus 后端：与 FaissStore 同接口（VectorStore 协议），pymilvus 为可选依赖（lazy import）。

client 参数注入鸭子类型对象（测试）；None 时 lazy import 真实 MilvusClient（连接参数走 env/默认 localhost）。
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from typing import Any

from ..exceptions import SluiceError
from .store import EmbedFn, VectorHit, _l2_normalize


class MilvusStore:
    def __init__(self, collection: str, dim: int, embed: EmbedFn, client: Any = None):
        self._collection = collection
        self._dim = dim
        self._embed = embed
        if client is None:
            if importlib.util.find_spec("pymilvus") is None:
                raise SluiceError("pymilvus 未安装：pip install pymilvus（Milvus 后端为可选，FAISS 为默认）")
            from pymilvus import MilvusClient

            client = MilvusClient()  # 连接参数走环境默认（本地演示）或 MILVUS_URI
        self._client = client
        if not client.has_collection(collection):
            client.create_collection(collection, dimension=dim, metric_type="IP", id_type="string")

    def upsert(self, texts: list[str], metas: list[dict] | None = None) -> None:
        if not texts:
            return
        metas = metas or [{} for _ in texts]
        vecs = self._embed(texts)
        if len(vecs) != len(texts):
            raise ValueError(
                f"embedder 返回条数({len(vecs)})与输入条数({len(texts)})不符，拒绝写入以防文本-向量错位"
            )
        rows = [
            {
                "id": str(uuid.uuid4()),
                "collection": self._collection,
                "vector": _l2_normalize(v),
                "text": t,
                "meta_json": json.dumps(m, ensure_ascii=False),
            }
            for t, m, v in zip(texts, metas, vecs)
        ]
        self._client.insert(self._collection, rows)

    def search(self, query: str, k: int = 5) -> list[VectorHit]:
        vec = _l2_normalize(self._embed([query])[0])
        results = self._client.search(
            self._collection, data=[vec], limit=k, output_fields=["text", "meta_json"]
        )
        hits = []
        for row in (results[0] if results else []):
            entity = row.get("entity", {})
            hits.append(
                VectorHit(
                    text=entity.get("text", ""),
                    score=float(row.get("distance", 0.0)),
                    meta=json.loads(entity.get("meta_json", "{}")),
                )
            )
        return hits

    def __len__(self) -> int:
        return 0  # Milvus 侧计数需 query；本接口演示不依赖，明确返回 0 并注释
