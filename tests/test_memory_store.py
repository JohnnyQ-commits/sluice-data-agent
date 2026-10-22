"""向量记忆测试：Store 抽象 + FAISS Flat 后端 + Embedder 注入（HashEmbedder 零网络）。"""

import json

import httpx

from sluice.memory.store import FaissStore, GatewayEmbedder, HashEmbedder, VectorHit


def test_hash_embedder_deterministic():
    e = HashEmbedder(dim=64)
    v1, v2 = e(["hello world"]), e(["hello world"])
    assert v1 == v2 and len(v1[0]) == 64
    assert max(abs(x) for x in v1[0]) <= 1.0 + 1e-9  # 已归一化


def test_faiss_store_upsert_and_search(tmp_path):
    store = FaissStore(embed=HashEmbedder(dim=64), dim=64, path=str(tmp_path / "v"))
    store.upsert(
        ["上周销售额环比上升", "杭州连续三周居首", "信贷通过率下降"],
        metas=[{"src": "a"}, {"src": "b"}, {"src": "c"}],
    )
    # 查询与第一条文本完全相同：HashEmbedder 按空白切分，连续中文整句是单 token，
    # 词面重合相似度只能来自"查询整 token == 文本整 token"的精确命中，才能保证排序确定性。
    hits = store.search("上周销售额环比上升", k=2)
    assert len(hits) == 2
    assert all(isinstance(h, VectorHit) for h in hits)
    assert hits[0].meta == {"src": "a"}  # 与查询完全同 token 的排第一
    assert hits[0].score >= hits[1].score


def test_faiss_store_persist_reload(tmp_path):
    path = str(tmp_path / "v")
    store = FaissStore(embed=HashEmbedder(dim=64), dim=64, path=path)
    store.upsert(["一条记忆"], metas=[{"k": "v"}])
    store2 = FaissStore(embed=HashEmbedder(dim=64), dim=64, path=path)
    assert len(store2) == 1
    hits = store2.search("一条记忆", k=1)
    assert hits and hits[0].text == "一条记忆" and hits[0].meta == {"k": "v"}


def test_faiss_store_search_empty_index(tmp_path):
    store = FaissStore(embed=HashEmbedder(dim=64), dim=64, path=str(tmp_path / "v"))
    assert store.search("任意", k=3) == []


def test_faiss_store_default_metas_and_k_clamp(tmp_path):
    store = FaissStore(embed=HashEmbedder(dim=64), dim=64, path=str(tmp_path / "v"))
    store.upsert(["alpha beta", "gamma delta"])  # 不传 metas → 默认空 dict
    hits = store.search("alpha beta", k=10)  # k 超过索引条数 → 返回全部 2 条，不报错
    assert len(hits) == 2
    assert all(h.meta == {} for h in hits)
    assert hits[0].text == "alpha beta" and hits[0].score > hits[1].score


def test_gateway_embedder_parses_openai_style():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/embeddings")
        body = json.loads(request.content)
        assert body["model"] == "embedding-1" and body["input"] == ["你好"]
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})

    embed = GatewayEmbedder(
        base_url="https://gw.example",
        api_key="k",
        model="embedding-1",
        transport=httpx.MockTransport(handler),
    )
    assert embed(["你好"]) == [[0.1, 0.2]]
