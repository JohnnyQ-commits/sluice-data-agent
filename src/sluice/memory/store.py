"""向量记忆（语义维度）：EmbedFn 注入 + VectorStore 抽象 + FAISS Flat 后端。

EmbedFn: Callable[[list[str]], list[list[float]]]——测试用 HashEmbedder（零网络确定性），
生产可配 GatewayEmbedder（网关 /v1/embeddings，OpenAI 兼容形态，模型 settings.memory_embed_model）。
后端可换（FAISS/Milvus 同接口）——抽象层价值的活演示。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

EmbedFn = Callable[[list[str]], list[list[float]]]


@dataclass
class VectorHit:
    text: str
    score: float
    meta: dict[str, Any] = field(default_factory=dict)


class VectorStore(Protocol):
    def upsert(self, texts: list[str], metas: list[dict] | None = None) -> None: ...
    def search(self, query: str, k: int = 5) -> list[VectorHit]: ...
    def __len__(self) -> int: ...


class HashEmbedder:
    """确定性伪向量：token hash 分桶 + L2 归一化。测试与无配置降级用——词面重合才相似，无语义。"""

    def __init__(self, dim: int = 64):
        self.dim = dim

    def __call__(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in text.split():
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        return _l2_normalize(vec)


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FaissStore:
    """FAISS Flat 内积索引（向量已归一化 → 内积即 cosine）。元数据随索引存 JSON。"""

    def __init__(self, embed: EmbedFn, dim: int, path: str | None = None):
        import faiss  # dev 依赖；延迟导入避免未装环境炸 import 链
        import numpy as np

        self._np = np
        self._embed = embed
        self._dim = dim
        self._index = faiss.IndexFlatIP(dim)
        self._texts: list[str] = []
        self._metas: list[dict] = []
        self._faiss = faiss
        self._path = Path(path) if path else None
        if self._path and self._path.with_suffix(".faiss").is_file():
            self._index = faiss.read_index(str(self._path.with_suffix(".faiss")))
            meta_file = self._path.with_suffix(".meta.json")
            payload = json.loads(meta_file.read_text(encoding="utf-8"))
            self._texts = payload["texts"]
            self._metas = payload["metas"]
            if self._index.ntotal != len(self._texts):
                raise RuntimeError(
                    f"向量存储加载失败：索引条数({self._index.ntotal})与元数据条数({len(self._texts)})不一致，"
                    f"上次持久化可能半写残留（{self._path.with_suffix('.faiss')} vs "
                    f"{self._path.with_suffix('.meta.json')}），请删除这两个文件后重建索引"
                )

    def upsert(self, texts: list[str], metas: list[dict] | None = None) -> None:
        if not texts:
            return
        metas = metas or [{} for _ in texts]
        vecs = self._np.array(self._embed(texts), dtype="float32")
        if vecs.shape[0] != len(texts):
            raise ValueError(
                f"embedder 返回条数({vecs.shape[0]})与输入条数({len(texts)})不符，拒绝写入以防文本-向量错位"
            )
        self._faiss.normalize_L2(vecs)
        self._index.add(vecs)
        self._texts.extend(texts)
        self._metas.extend(metas)
        self._save()

    def search(self, query: str, k: int = 5) -> list[VectorHit]:
        if len(self._texts) == 0:
            return []
        vec = self._np.array(self._embed([query]), dtype="float32")
        self._faiss.normalize_L2(vec)
        scores, idx = self._index.search(vec, min(k, len(self._texts)))
        hits = []
        for score, i in zip(scores[0], idx[0]):
            if i == -1:
                continue
            hits.append(VectorHit(text=self._texts[i], score=float(score), meta=self._metas[i]))
        return hits

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._faiss.write_index(self._index, str(self._path.with_suffix(".faiss")))
        self._path.with_suffix(".meta.json").write_text(
            json.dumps({"texts": self._texts, "metas": self._metas}, ensure_ascii=False), encoding="utf-8"
        )

    def __len__(self) -> int:
        return len(self._texts)


class GatewayEmbedder:
    """网关 embedding（OpenAI 兼容 /v1/embeddings）。端点形态首次真跑需探针确认（见 T12 备注）。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0, transport: Any = None):
        import httpx

        self._model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def __call__(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.post("/v1/embeddings", json={"model": self._model, "input": texts})
        if resp.status_code >= 400:
            raise ValueError(f"embeddings 请求失败 HTTP {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        if "data" not in payload:
            raise ValueError(f"embeddings 响应缺 'data' 字段: {resp.text[:200]}")
        items = sorted(payload["data"], key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in items]
