# 网关 embedding 端点探针报告

- 日期：2026-10-22
- 探针脚本：`scripts/probe_embeddings.py`（venv python -X utf8 真实运行）
- 结论先行：**`/v1/embeddings` 端点在网关存在且行为是 OpenAI 兼容转发，但网关当前没有配置任何 embedding 模型 —— GatewayEmbedder 状态：待端点就绪，暂不可用。**

## 实测过程

探针从 `.env` 读 `SLUICE_LLM_BASE_URL` / `SLUICE_LLM_API_KEY`（手解析，不继承进程环境，运行前清 `ANTHROPIC_*`），POST `{base_url}/v1/embeddings`，请求体 `{"model": ..., "input": ["测试"]}`，超时 30s。

| 尝试模型 | HTTP | 结果 |
|---|---|---|
| `SLUICE_LLM_MODEL`（chat 模型，厂商前缀形态） | 400 | 网关把请求转发到了上游 `…/api/paas/v4/embeddings`，上游返回 `code 1210 API 调用参数有误`（chat 模型不是 embedding 模型，但证明**转发链路存在**） |
| `zhipu/embedding-3` | 404 | `model_not_found` / `Model configuration not found`（网关层拒绝，未配置该模型） |
| `embedding-3` | 404 | 同上 |
| `zhipu/embedding-2` | 404 | 同上 |

（base_url 为公司内部网关，按脱敏铁律不以字面量写入本文档；探针输出中可见。）

## 关键事实

1. **端点路径**：`/v1/embeddings` 存在于网关，非 404 路由级缺失。请求体形状与 `GatewayEmbedder` 假设一致（OpenAI 兼容：`model` + `input`）。
2. **请求体形状确认**：chat 模型名被网关接受并转发上游（400 是上游对非 embedding 模型的参数拒绝），说明网关的 `/v1/embeddings` 是真实代理而非静态报错。
3. **返回结构**：未能拿到 200 样本 —— 所有 embedding 候选模型名都在网关层 404 `model_not_found`。因此 `model`/`object`/`data[0].embedding` 维度/`usage` 均无法实测。
4. **模型路由**：网关按"厂商前缀模型名"路由（如 `zhipu/…`），但当前模型配置清单里没有任何 embedding 模型。待网关侧开通 embedding 模型（预期形态 `zhipu/embedding-3` 或同类）后重跑探针即可。

## 与 FaissStore 默认 dim 64 的关系

- FaissStore 的 `dim` 是构造参数（`FaissStore(embed, dim, path)`），**不是硬编码**；64 只是 `HashEmbedder` 的默认维度，两者独立。
- 端点就绪后接线路径（零代码改动）：`.env` 设 `SLUICE_MEMORY_EMBED_MODEL=<网关 embedding 模型名>`，构造 `GatewayEmbedder(base_url, api_key, model)` + `FaissStore(embed=…, dim=<探针实测的 embedding 维度>)`。探针脚本会直接打印实测维度。
- 注意：dim 必须与 embedder 返回维度一致（MilvusStore 已有文本-向量条数一致性防错位校验；FaissStore 侧维度不匹配会在 add 时报错），切换 embedder 后旧索引需重建。

## GatewayEmbedder 标注

`GatewayEmbedder` 代码本身无需改动（端点路径与请求体形状已被本次探针证实与实现一致）。当前不可用的唯一原因是网关侧无 embedding 模型可配 —— 状态记为**待端点就绪**。重跑方式：

```bash
.venv/Scripts/python -X utf8 scripts/probe_embeddings.py <模型名>
```
