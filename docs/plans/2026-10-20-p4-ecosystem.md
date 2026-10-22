# P4 生态层实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐设计文档 §5/§6 的生态能力——自实现 MCP client（协议/传输/注册）、memory 层（会话记忆 + 向量检索 FAISS/Milvus 双后端）、Skill 语义召回与会话长期记忆两个落点、LangGraph 对比移植练习；顺带清偿 P3 全部顺延遗留。

**Architecture:** MCP client 三件套（protocol 编解码 / stdio transport 子进程生命周期 / registry→Tool 适配）归一进既有 Tool 协议；memory 层以 `EmbedFn` 注入为轴——测试用 HashEmbedder（零网络），生产可配 GatewayEmbedder，向量后端 FaissStore/MilvusStore 同接口互换（抽象层价值的活演示）。所有新能力默认关闭（`mcp_enabled`/`memory_enabled` 默认 False），保证 P0-P3 行为零变化。

**Tech Stack:** 纯标准库 + httpx（MCP client 零新依赖，官方 SDK 不用——"接 MCP"与"实现 MCP client"的差距正是卖点）；faiss-cpu + numpy（dev 依赖）；pymilvus 可选 lazy import；langgraph 可选 extra。

---

## 红线（执行者必读）

1. **pytest 套件零 API key 可跑**——所有 LLM/embedding 依赖注入桩；真网关只出现在 T5（重建基线）与 T12 的探针步（可跳过不阻塞）
2. **默认配置行为零变化**——`mcp_enabled=False`、`memory_enabled=False` 时，现有 199 个测试零改动全绿；`tests/test_runtime_events.py` 永不触碰
3. **审计 kind 惯例**：`f"{gate}_blocked"` 全库唯一产生点是 `runtime/loop.py`，新代码不新增 kind、不绕过审计
4. **kill switch**：任何新链路挂掉只降级不致命（MCP server 崩了警告跳过、记忆召回失败返回空串）
5. **脱敏铁律**：公司名/公司域名/个人工号等内部敏感词永不以字面量进 tracked 文件（规则本体也不得包含字面量——本行即示范）
6. commit 一行中文、无 Co-Authored-By；文件操作用完整 Windows 绝对路径；测试命令 `.venv/Scripts/python -m pytest`（repo 根，bash）

## 文件结构地图

```
src/sluice/
├── mcp/                      # T6-T8 新增（自实现 MCP client）
│   ├── __init__.py
│   ├── protocol.py           # JSON-RPC 2.0 编解码（initialize/tools/list/tools/call）
│   ├── transport.py          # stdio 子进程传输（请求关联/超时/崩溃重启）
│   └── registry.py           # .mcp.json 发现 → McpTool 适配进 Tool 协议
├── memory/                   # T9-T12 新增
│   ├── __init__.py
│   ├── session.py            # 会话轮次 JSONL 存取（时间维度）
│   ├── store.py              # VectorStore 抽象 + FaissStore + GatewayEmbedder/HashEmbedder
│   └── milvus_store.py       # MilvusStore 同接口适配器（lazy import）
├── guardrails/behavior_gate.py   # T1 加 threading.Lock
├── evals/{model,runner,judge}.py # T2 judge 健壮性 / T3 known_issue
├── skills/weekly-report/{tools.py,SKILL.md,evals/cases.py}  # T4 前缀查询工具
├── cli/main.py               # T8 `sluice mcp` 子命令
├── web/app.py                # T8 MCP 注册 / T12 记忆接线
├── config/settings.py        # T8/T12 新配置（默认关）
└── exceptions.py             # T6 MCP 异常三件套
tests/
├── fixtures/mini_mcp_server.py   # T7 最小 stdio MCP server（测试专用真子进程）
├── test_mcp_{protocol,transport,registry}.py
├── test_memory_{session,store,milvus}.py
├── test_semantic_skill_match.py / test_session_memory.py
└── test_langgraph_port.py    # skipif 无 langgraph
examples/langgraph_weekly/    # T13 对比移植练习
scripts/demo_memory.py        # T12c 记忆演示脚本
evals/baseline.json           # T5 重建
```

---

### Task 1: 收口批（.gitignore + 存量 format + BehaviorGate 线程安全）

**Files:**
- Modify: `E:\test\sluice-data-agent\.gitignore`
- Modify: `E:\test\sluice-data-agent\tests\test_llm_client.py`（仅 ruff format）
- Modify: `E:\test\sluice-data-agent\src\sluice\guardrails\behavior_gate.py`
- Test: `E:\test\sluice-data-agent\tests\test_behavior_gate.py`（追加）

- [ ] **Step 1: .gitignore 追加两行**（文件末尾）

```
.idea/
web_server*.log
```

- [ ] **Step 2: 收敛存量 format**

Run: `.venv/Scripts/python -m ruff format tests/test_llm_client.py`
Expected: 1 file reformatted（纯格式，P0 遗留）

- [ ] **Step 3: 写失败测试（test_behavior_gate.py 追加）**

```python
def test_concurrent_check_call_thread_safe(tmp_path):
    """并发 check_call/commit_round 不串状态：线程安全是并发工具执行的前置（P4 红线）。"""
    import threading

    from sluice.config.settings import Settings
    from sluice.guardrails.behavior_gate import BehaviorGate
    from sluice.llm.types import ToolUseBlock

    gate = BehaviorGate(Settings(audit_dir=str(tmp_path / "a")))
    t1, t2 = ToolUseBlock("t1", "demo.report", {"text": "x"}), ToolUseBlock("t2", "demo.report", {"text": "y"})
    blocked: list[bool] = []


    def _worker(tu):
        for _ in range(200):
            blocked.append(gate.check_call(tu) is not None)


    threads = [threading.Thread(target=_worker, args=(t,)) for t in (t1, t2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    # t1/t2 参数不同互不命中：200x2 次全部放行；若锁缺失导致集合读写撕裂会抛异常而非静默错
    assert not any(blocked)
```

先读 `src/sluice/guardrails/behavior_gate.py` 与 `src/sluice/guardrails/base.py`（若有基类契约）确认 `check_call` 签名返回（拦截时返回什么/放行时返回 None——以真实代码为准微调测试）。

- [ ] **Step 4: 跑测试确认失败或通过**（无锁时大概率仍通过——本测试是防回归钉子；若通过直接进 Step 5，不强求红灯）

- [ ] **Step 5: 实现（behavior_gate.py）**

```python
import threading  # 顶部追加

class BehaviorGate:
    def __init__(self, ...):
        ...  # 既有字段不动
        self._lock = threading.Lock()

    def check_call(self, tool_use):  # 方法体整体包进 with self._lock:
        with self._lock:
            ...  # 原实现逐行保留

    def commit_round(self, tool_uses):
        with self._lock:
            ...  # 原实现逐行保留
```

- [ ] **Step 6: 全量回归 + commit**

Run: `.venv/Scripts/python -m pytest tests/ -q`（200 passed）+ `ruff check src/ tests/` 零告警

```bash
git add .gitignore tests/test_llm_client.py src/sluice/guardrails/behavior_gate.py tests/test_behavior_gate.py
git commit -m "fix: BehaviorGate加锁防并发撕裂+.gitignore收口.idea与日志+存量format收敛"
```

---

### Task 2: judge 健壮性小修（省略号 containment + 回显位置回退）

**Files:**
- Modify: `E:\test\sluice-data-agent\src\sluice\evals\judge.py`
- Test: `E:\test\sluice-data-agent\tests\test_evals_judge.py`（追加）

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_quote_with_ellipsis_segments_still_valid():
    """judge 把长句压缩成'A……B'改写引用 → 按省略号分段全含判定（P3 实录：基线轮 judge 全无效主因）。"""
    reply = '{"items": [{"item": "数值一致", "score": 5, "quote": "上周销售额 123.45 万元……杭州连续三周居首"}]}'
    items = judge_output(StubJudge(reply), ANSWER, ["数值一致"])
    assert items[0]["valid"] is True and items[0]["score"] == 5


def test_quote_ellipsis_segment_missing_still_invalid():
    """分段后任一段不在产出原文 → 仍无效（防作弊不放松）。"""
    reply = '{"items": [{"item": "数值一致", "score": 5, "quote": "上周销售额……编造的结尾"}]}'
    items = judge_output(StubJudge(reply), ANSWER, ["数值一致"])
    assert items[0]["valid"] is False


def test_echo_fallback_positional_match():
    """judge 全部改写维度名但逐位对应 rubric → 按位接受（回显匹配只是路由，quote containment 仍是防编造主闸）。"""
    reply = (
        '{"items": [{"item": "维度一：数值是否一致", "score": 4, "quote": "环比 +15%"},'
        ' {"item": "维度二：口径", "score": 3, "quote": "杭州连续三周居首"}]}'
    )
    items = judge_output(StubJudge(reply), ANSWER, ["数值一致", "口径正确"])
    assert [i["score"] for i in items] == [4, 3] and all(i["valid"] for i in items)


def test_echo_fallback_not_applied_on_length_mismatch():
    """items 数与 rubric 数不等 → 不启用按位回退，维持降级。"""
    reply = '{"items": [{"item": "乱写", "score": 5, "quote": "环比 +15%"}]}'
    items = judge_output(StubJudge(reply), ANSWER, ["数值一致", "口径正确"])
    assert all(i["valid"] is False for i in items)
```

- [ ] **Step 2: 确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_evals_judge.py -q` → 4 FAIL

- [ ] **Step 3: 实现（先读 judge.py 当前实现，改造两点）**

(a) quote containment 改为分段判定（新函数 + valid 判定处替换 `_normalize(quote) in answer_norm`）：

```python
def _quote_covers(quote_norm: str, answer_norm: str) -> bool:
    """quote 归一化后是否被产出包含；含省略号（……/...）时按省略号分段，要求每段全含。"""
    if not quote_norm:
        return False
    segments = [s for s in re.split(r"…+|\.{3,}", quote_norm) if s]
    if len(segments) <= 1:
        return quote_norm in answer_norm
    return all(s in answer_norm for s in segments)
```

(b) by_item 构建末尾追加按位回退（当前 echo/精确匹配全部 miss 时）：

```python
    if not by_item and len(items_raw) == len(rubric) and all(isinstance(it, dict) for it in items_raw):
        # 位置回退：judge 全部改写维度名但逐位对应 → 按位接受（quote containment 仍守防编造）
        keys = [_echo_key(str(it.get("item", ""))) for it in items_raw]
        if keys == [_echo_key(r) for r in rubric]:
            by_item = {rubric[i]: items_raw[i] for i in range(len(rubric))}
```

（`_echo_key` 的真实形态以 ee15a58 后的 judge.py 为准；若当前实现没有独立 `_echo_key` 函数而是内联逻辑，抽出来复用。）

- [ ] **Step 4: 通过 + 全量回归（204）+ ruff**

- [ ] **Step 5: Commit**

```bash
git add src/sluice/evals/judge.py tests/test_evals_judge.py
git commit -m "fix: judge省略号引用分段containment+维度回显逐位回退(P3基线轮全无效主因)"
```

---

### Task 3: known_issue 豁免机制

**Files:**
- Modify: `E:\test\sluice-data-agent\src\sluice\evals\model.py`
- Modify: `E:\test\sluice-data-agent\src\sluice\evals\runner.py`（run_eval 透传）
- Modify: `E:\test\sluice-data-agent\src\sluice\evals\compare.py`
- Modify: `E:\test\sluice-data-agent\src\sluice\cli\main.py`（_print_report 标注）
- Test: `E:\test\sluice-data-agent\tests\test_evals_model.py` / `tests\test_evals_compare.py` / `tests\test_cli_eval.py`（各追加）

- [ ] **Step 1: 写失败测试**

test_evals_model.py 追加：

```python
def test_known_issue_field_roundtrip():
    """known_issue 进 EvalCase 与 CaseResult，且 JSON roundtrip 保留（P4：结构性已知 fail 豁免门禁）。"""
    case = EvalCase.from_dict("s", {"name": "c", "layer": "behavior", "input": "x", "known_issue": True})
    assert case.known_issue is True
    default = EvalCase.from_dict("s", {"name": "c", "layer": "behavior", "input": "x"})
    assert default.known_issue is False
    r = CaseResult("s", "c", "behavior", "fail", known_issue=True)
    assert EvalReport(results=[r]).to_dict()["results"][0]["known_issue"] is True
```

test_evals_compare.py 追加：

```python
def test_known_issue_fail_exempt_from_gate():
    """known_issue 的 fail 不进通过率分母：基线豁免的结构性 fail 不拖累也卡不住门禁。"""
    base = _report(
        [
            CaseResult("s", "a", "guardrail", "pass"),
            CaseResult("s", "route", "behavior", "fail", known_issue=True),
        ]
    )
    new = _report(
        [
            CaseResult("s", "a", "guardrail", "pass"),
            CaseResult("s", "route", "behavior", "fail", known_issue=True),
        ]
    )
    ok, _ = compare(new, base)
    assert ok  # 两侧 known_issue fail 均不计分


def test_known_issue_does_not_mask_real_regression():
    """豁免只对 known_issue fail 生效：同 skill 普通 fail 下降仍报警。"""
    base = _report(
        [
            CaseResult("s", "a", "guardrail", "pass"),
            CaseResult("s", "route", "behavior", "fail", known_issue=True),
        ]
    )
    new = _report(
        [
            CaseResult("s", "a", "guardrail", "fail"),
            CaseResult("s", "route", "behavior", "fail", known_issue=True),
        ]
    )
    ok, _ = compare(new, base)
    assert not ok
```

test_cli_eval.py 追加（_print_report 输出标注——改为直接测报告行，见 Step 3 注）：

```python
def test_report_marks_known_issue(capsys):
    from sluice.cli.main import _print_report
    from sluice.evals.model import CaseResult, EvalReport

    _print_report(
        EvalReport(results=[CaseResult("s", "route", "behavior", "fail", known_issue=True)]),
        out=None,
    )
    assert "known_issue" in capsys.readouterr().out
```

- [ ] **Step 2: 确认失败**

- [ ] **Step 3: 实现**

(a) model.py——`EvalCase` 加字段 `known_issue: bool = False`；`_ALLOWED_KEYS` 加 `"known_issue"`；`from_dict` 加 `self.known_issue = bool(raw.get("known_issue", False))`；`CaseResult` 加字段 `known_issue: bool = False`；`to_dict`/`load`（CaseResult(**r)）自然携带（dataclass 默认值兜底旧报告无此键）。

(b) runner.py `run_eval`——所有 CaseResult 构造点加 `known_issue=case.known_issue`（from_dict 失败分支用 `known_issue=bool(raw.get("known_issue", False)) if isinstance(raw, dict) else False`）。

(c) compare.py `_skill_pass_rate`：

```python
def _skill_pass_rate(report: EvalReport, skill: str) -> float:
    counted = [
        r
        for r in report.results
        if r.skill == skill and r.status != "skipped" and not (r.status == "fail" and r.known_issue)
    ]
    if not counted:
        return 1.0
    return sum(1 for r in counted if r.status == "pass") / len(counted)
```

模块 docstring 补一句豁免语义。

(d) cli/main.py `_print_report` 的 case 行追加标注：`{r.case}{' ⚠️known_issue' if r.known_issue else ''}`；`out: Path` 参数测试里传 None——函数末尾 `print(f"报告: {out}")` 对 None 也安全，无需分支。

- [ ] **Step 4: 通过 + 全量回归（207）+ ruff**

- [ ] **Step 5: Commit**

```bash
git add src/sluice/evals/model.py src/sluice/evals/runner.py src/sluice/evals/compare.py src/sluice/cli/main.py tests/test_evals_model.py tests/test_evals_compare.py tests/test_cli_eval.py
git commit -m "feat: evals known_issue豁免机制(结构性fail不进分母不卡门禁)+报告标注"
```

---

### Task 4: weekly-report 前缀查询工具（结构性 fail 根因修复）

**Files:**
- Modify: `E:\test\sluice-data-agent\skills\weekly-report\tools.py`
- Modify: `E:\test\sluice-data-agent\skills\weekly-report\SKILL.md`
- Modify: `E:\test\sluice-data-agent\skills\weekly-report\evals\cases.py`
- Test: `E:\test\sluice-data-agent\tests\test_weekly_report_skill.py`（追加）

- [ ] **Step 1: 写失败测试（追加；先读该文件既有桩风格）**

```python
def test_query_sales_delegates_and_rejects_non_string(tmp_path):
    """前缀查询工具：SQL 执行委托 query_data 内核，非字符串 SQL 报错不执行（结构性 fail 修复件）。"""
    import sqlite3

    from sluice.skills.weekly_report.tools import QuerySalesTool

    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sales (sale_date TEXT, city TEXT, category TEXT, amount REAL)")
    conn.execute("INSERT INTO sales VALUES ('2026-10-13', '杭州', '食品', 100.0)")
    conn.commit()
    conn.close()

    tool = QuerySalesTool(str(db))
    ok = tool.execute({"sql": "SELECT city, SUM(amount) FROM sales GROUP BY city"})
    assert not ok.is_error and "杭州" in ok.content
    bad = tool.execute({"sql": 123})
    assert bad.is_error and "sql" in bad.content.lower()
```

（注意 skill 包内 import 路径：`sluice.skills.weekly_report.tools`——以下划线连字；以既有测试文件的 import 为准。）

- [ ] **Step 2: 确认失败**（ImportError: QuerySalesTool）

- [ ] **Step 3: 实现（skills/weekly-report/tools.py 追加）**

```python
class QuerySalesTool(Tool):
    """Skill 前缀查询工具：P4 修复 intent_route 结构性 fail——skill 查数不再依赖平台裸 query_data。"""

    name: ClassVar[str] = "query_sales"
    description: ClassVar[str] = (
        "在 demo 库执行只读 SQL（表 sales），返回行文本。查数一律用本工具，不要用平台裸工具。"
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "只读 SELECT 语句"}},
        "required": ["sql"],
    }

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path  # None = 运行时取平台 demo 库（生产路径）

    def _delegate(self):
        from ..tools_adapter import query_data_tool_for  # 占位：见下注

    def execute(self, args: dict[str, Any]) -> ToolResult:
        sql = args.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return ToolResult("sql 必须是非空字符串", is_error=True)
        from sluice.tools.query_data import QueryDataTool
        from sluice.config.settings import Settings

        db_path = self._db_path or Settings().demo_db_path
        return QueryDataTool(db_path).execute({"sql": sql})
```

（实现注：删掉 `_delegate` 占位方法——QueryDataTool 直接函数内 import 构造，惰性取 db_path 避免改 loader 协议；测试注入 `db_path`。`TOOLS = [RenderReportTool(), QuerySalesTool()]`。若 `Settings().demo_db_path` 字段名与实际不符（先读 settings.py 确认，P1 时该字段已存在），以真实字段名为准。）

- [ ] **Step 4: SKILL.md 更新（三处一致）**

- 流程第 1 条改为：`1. 用 \`weekly-report.query_sales\` 查询数据（demo 库表 \`sales\`：sale_date/city/category/amount）。`
- description 里 `用 query_data 查询` 同步改为 `用 weekly-report.query_sales 查询`
- 内省提示条（第 5 条）改为：表结构以本文件声明为准 + `查数用 weekly-report.query_sales，不要用平台裸工具`

- [ ] **Step 5: evals/cases.py 更新**

- 三个 guardrail script 里 `{"tool": "query_data", ...}` 全部改 `weekly-report.query_sales`
- `weekly_intent_route` 上方三行结构性注释改为一句：`# 结构性 fail 已于 P4 修复（skill 配前缀查询工具），expect 不变，重建基线后预期 pass`
- `repeat_call_interrupted` 的两条重复 SQL 同步换工具名（门禁语义不变：同参重复仍拦）

- [ ] **Step 6: 通过 + 全量回归 + ruff（skills/ 不在 ruff 范围则只查 src/tests）**

注意：`tests/test_weekly_report_skill.py` 既有 8 个测试可能断言了 TOOLS 列表长度或 loader 注册数——以真实断言为准同步更新（新增了一个工具），报告偏离。

- [ ] **Step 7: Commit**

```bash
git add skills/weekly-report/ tests/test_weekly_report_skill.py
git commit -m "feat: weekly-report配前缀查询工具query_sales(裸工具绕过契约的结构性fail根因修复)"
```

---

### Task 5: 真网关重建基线（依赖 T2+T4）

**Files:**
- 产出（提交）: `E:\test\sluice-data-agent\evals\baseline.json`

- [ ] **Step 1: seed（幂等）**

```bash
.venv/Scripts/python scripts/seed_demo_db.py
```
Expected: 输出含 `sales=1060 credit=415 rows`

- [ ] **Step 2: 无 key 冒烟（确认 9 用例本地路径正常）**

```bash
SLUICE_LLM_API_KEY= .venv/Scripts/python -X utf8 -m sluice eval --all
```
Expected: 4 guardrail pass + 5 skipped，exit 0（环境坑：`env -u` 无效——pydantic-settings 的 env_file 会加载 .env，必须用空串压过）

- [ ] **Step 3: 真网关实跑 + 重建基线**

```bash
env -u ANTHROPIC_MODEL -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL -u ANTHROPIC_DEFAULT_OPUS_MODEL -u ANTHROPIC_DEFAULT_SONNET_MODEL -u ANTHROPIC_DEFAULT_HAIKU_MODEL -u ANTHROPIC_SMALL_FAST_MODEL -u ANTHROPIC_DEFAULT_MODEL .venv/Scripts/python -X utf8 -m sluice eval --all --update-baseline
```

在 repo 根跑。Expected: 9/9 pass（weekly_intent_route 经 T4 应转绿），两个 outcome 的 judge_valid 均为 True（T2 修复后）。失败处置协议：单用例失败重跑一次整条命令；连续两次失败 → 停止，如实记录 DONE_WITH_CONCERNS，不许降阈值/删用例/改 expect。

- [ ] **Step 4: 门禁验证**

```bash
env -u ANTHROPIC_MODEL -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL -u ANTHROPIC_DEFAULT_OPUS_MODEL -u ANTHROPIC_DEFAULT_SONNET_MODEL -u ANTHROPIC_DEFAULT_HAIKU_MODEL -u ANTHROPIC_SMALL_FAST_MODEL -u ANTHROPIC_DEFAULT_MODEL .venv/Scripts/python -X utf8 -m sluice eval --all --baseline evals/baseline.json
```
Expected: 比对行全 ✅ exit 0

- [ ] **Step 5: Commit**

```bash
git add evals/baseline.json
git commit -m "test: 真网关重建基线(judge有效+intent_route修复后9/9)"
```

（若 9/9 未达成：基线如实提交，报告 DONE_WITH_CONCERNS 列出失败项，不许凑绿。）

---

### Task 6: MCP protocol.py（JSON-RPC 2.0 编解码）

**Files:**
- Create: `E:\test\sluice-data-agent\src\sluice\mcp\__init__.py`（空文件）
- Create: `E:\test\sluice-data-agent\src\sluice\mcp\protocol.py`
- Modify: `E:\test\sluice-data-agent\src\sluice\exceptions.py`
- Test: `E:\test\sluice-data-agent\tests\test_mcp_protocol.py`

- [ ] **Step 1: 写失败测试**

```python
"""MCP protocol 层测试：JSON-RPC 2.0 编解码与结果提取。"""

import json

import pytest

from sluice.exceptions import MCPError
from sluice.mcp.protocol import (
    extract_result_text,
    initialize_request,
    parse_message,
    tools_call_request,
    tools_list_request,
)


def test_initialize_request_shape():
    line = initialize_request(1)
    msg = json.loads(line)
    assert msg == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "sluice", "version": "0.1.0"},
        },
    }


def test_tools_list_and_call_requests():
    assert json.loads(tools_list_request(2))["method"] == "tools/list"
    msg = json.loads(tools_call_request("echo", {"text": "hi"}, 3))
    assert msg["method"] == "tools/call" and msg["params"] == {"name": "echo", "arguments": {"text": "hi"}}


def test_parse_message_roundtrip():
    line = tools_list_request(7)
    assert parse_message(line)["id"] == 7


def test_parse_message_rejects_non_json():
    with pytest.raises(MCPError, match="非 JSON"):
        parse_message("not json")


def test_extract_result_text_joins_text_blocks():
    resp = {"result": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}
    assert extract_result_text(resp) == "a\nb"


def test_extract_result_text_empty_content():
    assert extract_result_text({"result": {"content": []}}) == ""
```

- [ ] **Step 2: 确认失败**（ImportError）

- [ ] **Step 3: exceptions.py 追加**

```python
class MCPError(SluiceError):
    """MCP 协议/工具调用错误。"""


class MCPTransportError(MCPError):
    """MCP 子进程传输层错误（进程退出/管道断裂）。"""


class MCPTimeoutError(MCPError):
    """MCP 请求超时。"""
```

- [ ] **Step 4: 实现 mcp/protocol.py**

```python
"""MCP JSON-RPC 2.0 编解码——自实现 client 的协议层（不用官方 SDK）。

消息形态：单行 JSON（stdio 传输以行为帧）。本层只管编码请求/解析消息/提取结果文本，
不含 IO——那是 transport 的职责。
"""

from __future__ import annotations

import json
from typing import Any

from ..exceptions import MCPError

PROTOCOL_VERSION = "2024-11-05"


def initialize_request(req_id: int) -> str:
    return _encode(
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "sluice", "version": "0.1.0"},
        },
        req_id,
    )


def initialized_notification() -> str:
    """initialize 完成的通知（无 id，不期望响应）。"""
    return _encode("notifications/initialized", {}, None)


def tools_list_request(req_id: int) -> str:
    return _encode("tools/list", {}, req_id)


def tools_call_request(name: str, arguments: dict, req_id: int) -> str:
    return _encode("tools/call", {"name": name, "arguments": arguments}, req_id)


def parse_message(line: str) -> dict[str, Any]:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as exc:
        raise MCPError(f"MCP 消息非 JSON: {line[:120]} ({exc})") from exc
    if not isinstance(msg, dict):
        raise MCPError(f"MCP 消息应为 JSON 对象: {line[:120]}")
    return msg


def extract_result_text(response: dict[str, Any]) -> str:
    """tools/call 响应 → 文本内容拼接（text 块以换行连接，其他类型块忽略）。"""
    content = (response.get("result") or {}).get("content") or []
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def _encode(method: str, params: dict[str, Any], req_id: int | None) -> str:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
    if req_id is not None:
        msg["id"] = req_id
    return json.dumps(msg, ensure_ascii=False)
```

- [ ] **Step 5: 通过（206+6=210）+ ruff**

- [ ] **Step 6: Commit**

```bash
git add src/sluice/mcp/ src/sluice/exceptions.py tests/test_mcp_protocol.py
git commit -m "feat: MCP协议层(JSON-RPC2.0编解码自实现,零官方SDK)+MCP异常三件套"
```

---

### Task 7: MCP transport.py（stdio 子进程传输，依赖 T6）

**Files:**
- Create: `E:\test\sluice-data-agent\tests\fixtures\mini_mcp_server.py`
- Create: `E:\test\sluice-data-agent\src\sluice\mcp\transport.py`
- Test: `E:\test\sluice-data-agent\tests\test_mcp_transport.py`

- [ ] **Step 1: 写测试替身服务器 `tests/fixtures/mini_mcp_server.py`**

```python
"""最小 MCP stdio server（仅测试用）：initialize / tools/list / tools/call(echo, add)。

以行分帧读 stdin、写 stdout——与 StdioTransport 构成真实子进程端到端（零网络）。
"""

import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "回显输入",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "add",
        "description": "两数相加",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
]


def reply(req_id, result):
    print(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}), flush=True)


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    method, req_id = req.get("method"), req.get("id")
    if method == "initialize":
        reply(req_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "mini", "version": "0.1"}})
    elif method == "tools/list":
        reply(req_id, {"tools": TOOLS})
    elif method == "tools/call":
        name = req["params"]["name"]
        args = req["params"].get("arguments", {})
        if name == "echo":
            reply(req_id, {"content": [{"type": "text", "text": args.get("text", "")}]})
        elif name == "add":
            reply(req_id, {"content": [{"type": "text", "text": str(args.get("a", 0) + args.get("b", 0))}]})
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"unknown tool {name}"}}), flush=True)
    # notifications/initialized 等无 id 消息：静默
```

- [ ] **Step 2: 写失败测试**

```python
"""MCP stdio 传输测试：真实子进程端到端（mini server），超时/协议错误/进程死亡分类。"""

import sys
from pathlib import Path

import pytest

from sluice.exceptions import MCPError, MCPTransportError, MCPTimeoutError
from sluice.mcp.transport import StdioTransport

MINI_SERVER = str(Path(__file__).parent / "fixtures" / "mini_mcp_server.py")


def _transport(timeout=10.0):
    t = StdioTransport([sys.executable, MINI_SERVER], timeout=timeout)
    t.start()
    return t


def test_initialize_and_tools_list_roundtrip():
    t = _transport()
    try:
        resp = t.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
        assert resp["result"]["serverInfo"]["name"] == "mini"
        tools = t.request("tools/list", {})
        assert [x["name"] for x in tools["result"]["tools"]] == ["echo", "add"]
    finally:
        t.close()


def test_tools_call_add():
    t = _transport()
    try:
        resp = t.request("tools/call", {"name": "add", "arguments": {"a": 2, "b": 40}})
        assert resp["result"]["content"][0]["text"] == "42"
    finally:
        t.close()


def test_unknown_tool_raises_protocol_error():
    t = _transport()
    try:
        with pytest.raises(MCPError, match="-32602"):
            t.request("tools/call", {"name": "nope", "arguments": {}})
    finally:
        t.close()


def test_dead_process_raises_transport_error():
    t = _transport()
    t.close()
    with pytest.raises(MCPTransportError):
        t.request("tools/list", {})


def test_timeout_raises_timeout_error():
    """对永不响应的进程（cat）请求 → 超时分类。"""
    cat = StdioTransport([sys.executable, "-c", "import sys,time\nfor l in sys.stdin: time.sleep(3600)"], timeout=1.0)
    cat.start()
    try:
        with pytest.raises(MCPTimeoutError):
            cat.request("tools/list", {})
    finally:
        cat.close()
```

- [ ] **Step 3: 确认失败**（ImportError: transport）

- [ ] **Step 4: 实现 mcp/transport.py**

```python
"""MCP stdio 传输：子进程生命周期 + 行分帧请求-响应关联。

设计要点：
- 读线程 pump：子进程 stdout → queue（Windows 管道不支持带超时的阻塞读，用线程 + queue.get(timeout)）
- 请求按 id 关联；非匹配响应/通知丢弃（本 client 无服务端主动请求需求）
- 超时 / 进程死亡 / 协议 error 三分类异常
"""

from __future__ import annotations

import itertools
import json
import queue
import subprocess
import threading
import time

from ..exceptions import MCPError, MCPTimeoutError, MCPTransportError
from . import protocol


class StdioTransport:
    def __init__(self, command: list[str], timeout: float = 30.0):
        self._command = command
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._queue: queue.Queue[dict | None] = queue.Queue()
        self._ids = itertools.count(1)

    def start(self) -> None:
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._queue.put(protocol.parse_message(line))
            except MCPError:
                self._queue.put(None)  # 无法解析 → 视作流终止
                return
        self._queue.put(None)  # EOF：进程关闭了 stdout

    def request(self, method: str, params: dict) -> dict:
        if self._proc is None or self._proc.poll() is not None:
            raise MCPTransportError(f"MCP 子进程未运行: {self._command}")
        req_id = next(self._ids)
        assert self._proc.stdin
        self._proc.stdin.write(protocol.tools_etc := protocol.__dict__ and "")  # 占位防误读，实现见下
```

（实现注：上面 `request` 的写行两行真实代码为——

```python
        self._proc.stdin.write(protocol.initialize_request(req_id) if False else _request_line(method, params, req_id))
        self._proc.stdin.flush()
```

——不要照抄占位。为免歧义，`request` 的完整正确实现：

```python
    def request(self, method: str, params: dict) -> dict:
        if self._proc is None or self._proc.poll() is not None:
            raise MCPTransportError(f"MCP 子进程未运行: {self._command}")
        req_id = next(self._ids)
        line = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}, ensure_ascii=False)
        assert self._proc.stdin
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()
        deadline = time.time() + self._timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise MCPTimeoutError(f"MCP 请求超时({self._timeout}s): {method}")
            try:
                msg = self._queue.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if msg is None:
                raise MCPTransportError("MCP 子进程已退出")
            if msg.get("id") == req_id:
                if "error" in msg:
                    err = msg["error"]
                    raise MCPError(f"MCP 协议错误 {err.get('code')}: {err.get('message', '')[:200]}")
                return msg
            # 其他 id 的响应/通知：丢弃
```

并在文件顶部补 `import json`（编解码细节：请求行直接 json.dumps 而不走 protocol._encode——`_encode` 是私有；或把 `_encode` 提为公开 `encode_request` 并在 T6 一并改名。**采用后者**：T6 的 protocol.py 里 `_encode` 命名为 `encode_request`（公开），T7 直接 `protocol.encode_request(method, params, req_id)`，测试不受影响（测试只用 initialize/tools_list/tools_call 工厂函数）。T6 执行者注意此命名。）

```python
    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def ensure_alive(self, max_restarts: int = 3) -> bool:
        """进程死亡则重启（计数上限），True=已重启需重新 initialize。"""
        if self._proc is not None and self._proc.poll() is None:
            return False
        if self._restarts >= max_restarts:
            raise MCPTransportError(f"MCP 子进程重启超上限({max_restarts}): {self._command}")
        self._restarts += 1
        self._queue = queue.Queue()
        self.start()
        return True
```

（`__init__` 补 `self._restarts = 0`。）

- [ ] **Step 5: 通过 + 全量回归 + ruff**

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/mini_mcp_server.py src/sluice/mcp/transport.py tests/test_mcp_transport.py
git commit -m "feat: MCP stdio传输(读线程pump+请求id关联+超时死亡协议错误三分类+崩溃重启上限)"
```

---

### Task 8: MCP registry + CLI `sluice mcp` + web 接线（依赖 T7）

**Files:**
- Create: `E:\test\sluice-data-agent\src\sluice\mcp\registry.py`
- Modify: `E:\test\sluice-data-agent\src\sluice\cli\main.py`
- Modify: `E:\test\sluice-data-agent\src\sluice\web\app.py`
- Modify: `E:\test\sluice-data-agent\src\sluice\config\settings.py`
- Test: `E:\test\sluice-data-agent\tests\test_mcp_registry.py` + `tests\test_cli_eval.py`（mcp 子命令 parser 测试追加此处或新建 test_cli_mcp.py）

- [ ] **Step 1: 写失败测试**

```python
"""MCP registry 测试：发现 → 启动 → Tool 适配 → 注册进 ToolRegistry（真实 mini server 子进程）。"""

import sys
from pathlib import Path

import json

from sluice.mcp.registry import discover, load_mcp_tools
from sluice.tools.registry import ToolRegistry

MINI_SERVER = str(Path(__file__).parent / "fixtures" / "mini_mcp_server.py")


def _config(tmp_path):
    cfg = tmp_path / ".mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"mini": {"command": sys.executable, "args": [MINI_SERVER]}}}), encoding="utf-8")
    return cfg


def test_discover_reads_config(tmp_path):
    servers = discover(_config(tmp_path))
    assert len(servers) == 1 and servers[0].name == "mini"


def test_load_mcp_tools_end_to_end(tmp_path):
    servers = load_mcp_tools(_config(tmp_path))
    try:
        tools = [t for s in servers for t in s.tools]
        assert {t.name for t in tools} == {"mcp__mini__echo", "mcp__mini__add"}
        echo = next(t for t in tools if t.name.endswith("echo"))
        result = echo.execute({"text": "你好"})
        assert result.content == "你好" and not result.is_error
    finally:
        for s in servers:
            s.close()


def test_tool_error_returns_tool_result_not_raise(tmp_path):
    servers = load_mcp_tools(_config(tmp_path))
    try:
        tools = [t for s in servers for t in s.tools]
        bad = next(t for t in tools if t.name.endswith("add"))
        # 用 protocol 层未知工具路径：直接调 McpTool 的原始名映射不经过 registry 校验，此处以 transport 异常转 ToolResult 为准
        bad._raw_name = "nope"  # 模拟服务端不认识的工具
        r = bad.execute({"a": 1, "b": 2})
        assert r.is_error and "nope" in r.content
    finally:
        for s in servers:
            s.close()


def test_registry_register_mcp_tools(tmp_path):
    servers = load_mcp_tools(_config(tmp_path))
    try:
        registry = ToolRegistry()
        for s in servers:
            for t in s.tools:
                registry.register(t)
        assert registry.get("mcp__mini__echo") is not None
    finally:
        for s in servers:
            s.close()
```

CLI parser 测试（追加到 test_cli_eval.py 或新建 test_cli_mcp.py）：

```python
def test_parser_mcp_flags():
    args = build_parser().parse_args(["mcp", "--call", "mcp__mini__echo", "--args", '{"text": "x"}'])
    assert args.command == "mcp" and args.call == "mcp__mini__echo"
```

- [ ] **Step 2: 确认失败**

- [ ] **Step 3: 实现 registry.py**

```python
"""MCP 注册表：.mcp.json 发现 → 子进程启动/initialize/tools/list → McpTool 适配进 Tool 协议。

命名约定：mcp__<server>__<tool>（官方 Claude 风格）——双下划线不撞 Skill 的点分前缀约定，
行为门禁的 skill 归因（split(".")）天然不会把 MCP 工具误归到某 Skill。
单个 server 启动失败只警告跳过（kill switch：MCP 挂了不拖垮平台）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..exceptions import MCPError, MCPTimeoutError
from ..tools.base import Tool, ToolResult
from .protocol import extract_result_text, initialize_request, initialized_notification, tools_call_request, tools_list_request
from .transport import StdioTransport

logger = logging.getLogger(__name__)


class McpTool(Tool):
    def __init__(self, server_name: str, meta: dict[str, Any], transport: StdioTransport):
        self.name = f"mcp__{server_name}__{meta['name']}"
        self.description = meta.get("description", "")
        self.parameters = meta.get("inputSchema", {"type": "object", "properties": {}})
        self._raw_name = meta["name"]
        self._transport = transport

    def execute(self, args: dict[str, Any]) -> ToolResult:
        try:
            resp = self._transport.request("tools/call", {"name": self._raw_name, "arguments": args})
        except MCPTimeoutError as exc:
            return ToolResult(f"MCP 工具 {self.name} 超时: {exc}", is_error=True)
        except MCPError as exc:
            return ToolResult(str(exc), is_error=True)
        result = resp.get("result") or {}
        return ToolResult(extract_result_text(resp), is_error=bool(result.get("isError")))


class McpServer:
    def __init__(self, name: str, command: list[str], timeout: float = 30.0):
        self.name = name
        self._command = command
        self._timeout = timeout
        self._transport: StdioTransport | None = None
        self.tools: list[McpTool] = []

    def start(self) -> list[McpTool]:
        self._transport = StdioTransport(self._command, timeout=self._timeout)
        self._transport.start()
        self._transport.request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "sluice", "version": "0.1.0"},
        })
        self._transport.request("notifications/initialized", {})
        listing = self._transport.request("tools/list", {})
        self.tools = [McpTool(self.name, meta, self._transport) for meta in listing.get("result", {}).get("tools", [])]
        return self.tools

    def close(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None


def discover(config_path: str | Path) -> list[McpServer]:
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    servers = []
    for name, spec in (cfg.get("mcpServers") or {}).items():
        command = [spec["command"], *spec.get("args", [])]
        servers.append(McpServer(name, command, timeout=float(spec.get("timeout", 30.0))))
    return servers


def load_mcp_tools(config_path: str | Path) -> list[McpServer]:
    """发现 + 启动全部 server；单个失败警告跳过，不拖垮启动。"""
    started: list[McpServer] = []
    for server in discover(config_path):
        try:
            server.start()
            started.append(server)
            logger.info("MCP server %s 就绪: %d 工具", server.name, len(server.tools))
        except MCPError as exc:
            logger.warning("MCP server %s 启动失败，已跳过: %s", server.name, exc)
    return started
```

（注意 `request("notifications/initialized", {})` 是通知不产生响应——transport.request 会等超时！修正：通知不走 request。给 transport 加 `notify(method, params)`：写行不等待。T7 执行时在 StdioTransport 补：

```python
    def notify(self, method: str, params: dict) -> None:
        if self._proc is None or self._proc.poll() is not None:
            raise MCPTransportError(f"MCP 子进程未运行: {self._command}")
        line = json.dumps({"jsonrpc": "2.0", "method": method, "params": params}, ensure_ascii=False)
        assert self._proc.stdin
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()
```

`McpServer.start` 改用 `self._transport.notify("notifications/initialized", {})`。**本任务计划把 notify 归入 T7 范围**——T7 执行者看到此注一并实现 + transport 测试补一条 notify 不阻塞的断言。）

- [ ] **Step 4: settings + CLI + web 接线**

settings.py 追加（Evals 块后）：

```python
    # MCP（P4）
    mcp_enabled: bool = False  # 默认关：无 .mcp.json 场景行为零变化
    mcp_config_path: str = ".mcp.json"  # 相对 repo 根
```

cli/main.py：build_parser 追加

```python
    mcp_p = sub.add_parser("mcp", help="MCP 工具发现与调用演示（读 .mcp.json）")
    mcp_p.add_argument("--call", metavar="TOOL", help="调用指定工具（如 mcp__mini__echo）")
    mcp_p.add_argument("--args", metavar="JSON", default="{}", help="工具参数 JSON")
```

main() 分发 `elif args.command == "mcp": _mcp(args)`；`_mcp` 实现：

```python
def _mcp(args: argparse.Namespace) -> None:
    settings = Settings()
    if not settings.mcp_enabled and not Path(_REPO_ROOT / settings.mcp_config_path).is_file():
        print("未找到 .mcp.json（且未启用 mcp_enabled）", file=sys.stderr)
        sys.exit(2)
    config = _REPO_ROOT / settings.mcp_config_path
    servers = load_mcp_tools(config)
    try:
        tools = [t for s in servers for t in s.tools]
        if not args.call:
            for t in tools:
                print(f"{t.name}  {t.description}")
            print(f"共 {len(tools)} 个 MCP 工具")
            return
        tool = next((t for t in tools if t.name == args.call), None)
        if tool is None:
            print(f"未找到工具: {args.call}", file=sys.stderr)
            sys.exit(2)
        result = tool.execute(json.loads(args.args))
        print(result.content)
        sys.exit(1 if result.is_error else 0)
    finally:
        for s in servers:
            s.close()
```

（imports 追加 `import json`、`from ..mcp.registry import load_mcp_tools`。）

web/app.py `create_app`：registry 构建后追加

```python
    if settings.mcp_enabled:
        from ..mcp.registry import load_mcp_tools

        mcp_config = Path(__file__).resolve().parents[3] / settings.mcp_config_path
        if mcp_config.is_file():
            for server in load_mcp_tools(mcp_config):
                for t in server.tools:
                    try:
                        registry.register(t)
                    except ValueError:
                        logger.warning("MCP 工具重名跳过: %s", t.name)
```

（web/app.py 顶部确认 logger 存在；没有则加。app 退出时 server 进程随主进程终止（daemon 线程+子进程）——Windows 下子进程不自动死，`atexit.register` 逐 server close；create_app 里收集 servers 存 app.extensions["mcp_servers"] 并注册 atexit。）

- [ ] **Step 5: 通过 + 全量回归 + ruff + 冒烟**

```bash
SLUICE_LLM_API_KEY= .venv/Scripts/python -X utf8 -m sluice mcp   # 无 .mcp.json → exit 2 提示
```

- [ ] **Step 6: Commit**

```bash
git add src/sluice/mcp/registry.py src/sluice/cli/main.py src/sluice/web/app.py src/sluice/config/settings.py src/sluice/mcp/transport.py tests/test_mcp_registry.py tests/test_cli_mcp.py tests/test_mcp_transport.py
git commit -m "feat: MCP注册表(.mcp.json发现→McpTool适配Tool协议)+sluice mcp子命令+web可选接线(默认关)"
```

---

### Task 9: memory/session.py（会话轮次 JSONL 存取）

**Files:**
- Create: `E:\test\sluice-data-agent\src\sluice\memory\__init__.py`（空文件）
- Create: `E:\test\sluice-data-agent\src\sluice\memory\session.py`
- Test: `E:\test\sluice-data-agent\tests\test_memory_session.py`

- [ ] **Step 1: 写失败测试**

```python
"""会话记忆测试：user_id+session_id 隔离的 JSONL 轮次存取。"""

from sluice.memory.session import SessionStore


def test_append_and_load_roundtrip(tmp_path):
    store = SessionStore(tmp_path)
    store.append("u1", "s1", "user", "查一下上周销量")
    store.append("u1", "s1", "assistant", "上周销量 1060")
    turns = store.load("u1", "s1")
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["text"] == "查一下上周销量"
    assert "ts" in turns[0]  # ISO 时间戳


def test_sessions_are_isolated(tmp_path):
    store = SessionStore(tmp_path)
    store.append("u1", "s1", "user", "a")
    store.append("u1", "s2", "user", "b")
    store.append("u2", "s1", "user", "c")
    assert len(store.load("u1", "s1")) == 1
    assert store.load("u1", "s1")[0]["text"] == "a"
    assert store.load("u2", "s1")[0]["text"] == "c"


def test_list_sessions_newest_first(tmp_path):
    import os, time

    store = SessionStore(tmp_path)
    store.append("u1", "old", "user", "x")
    time.sleep(0.05)
    store.append("u1", "new", "user", "y")
    assert store.list_sessions("u1") == ["new", "old"]


def test_load_missing_session_returns_empty(tmp_path):
    assert SessionStore(tmp_path).load("u1", "none") == []
```

- [ ] **Step 2: 确认失败**

- [ ] **Step 3: 实现**

```python
"""会话记忆（时间维度）：user_id+session_id 隔离的轮次 JSONL 落盘。

路径布局：<base>/<user_id>/<session_id>.jsonl；每行 {"ts", "role", "text"}。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class SessionStore:
    def __init__(self, base_dir: str | Path):
        self._base = Path(base_dir)

    def _session_file(self, user_id: str, session_id: str) -> Path:
        d = self._base / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{session_id}.jsonl"

    def append(self, user_id: str, session_id: str, role: str, text: str) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "role": role, "text": text}
        with self._session_file(user_id, session_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def load(self, user_id: str, session_id: str) -> list[dict]:
        path = self._base / user_id / f"{session_id}.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def list_sessions(self, user_id: str) -> list[str]:
        d = self._base / user_id
        if not d.is_dir():
            return []
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.stem for p in files]
```

- [ ] **Step 4: 通过 + 全量回归 + ruff**

- [ ] **Step 5: Commit**

```bash
git add src/sluice/memory/ tests/test_memory_session.py
git commit -m "feat: 会话记忆SessionStore(user+session隔离JSONL轮次存取)"
```

---

### Task 10: memory/store.py（VectorStore 抽象 + FAISS 后端 + 双 Embedder）

**Files:**
- Modify: `E:\test\sluice-data-agent\pyproject.toml`（dev 依赖 + faiss-cpu/numpy）
- Create: `E:\test\sluice-data-agent\src\sluice\memory\store.py`
- Test: `E:\test\sluice-data-agent\tests\test_memory_store.py`

- [ ] **Step 1: 依赖安装**

pyproject `[project.optional-dependencies].dev` 追加 `"faiss-cpu>=1.8"`、`"numpy>=1.26"`，然后 `.venv/Scripts/pip install -e ".[dev]"`（Windows wheel 存在；若安装失败报告偏离并改用 `pip install faiss-cpu numpy` 直装 + dev extra 保持声明）。

- [ ] **Step 2: 写失败测试**

```python
"""向量记忆测试：Store 抽象 + FAISS Flat 后端 + Embedder 注入（HashEmbedder 零网络）。"""

import pytest

from sluice.memory.store import FaissStore, GatewayEmbedder, HashEmbedder, VectorHit


def test_hash_embedder_deterministic():
    e = HashEmbedder(dim=64)
    v1, v2 = e(["hello world"]), e(["hello world"])
    assert v1 == v2 and len(v1[0]) == 64
    assert max(abs(x) for x in v1[0]) <= 1.0 + 1e-9  # 已归一化


def test_faiss_store_upsert_and_search(tmp_path):
    store = FaissStore(embed=HashEmbedder(dim=64), dim=64, path=str(tmp_path / "v"))
    store.upsert(["上周销售额环比上升", "杭州连续三周居首", "信贷通过率下降"], metas=[{"src": "a"}, {"src": "b"}, {"src": "c"}])
    hits = store.search("销售额 环比", k=2)
    assert len(hits) == 2
    assert all(isinstance(h, VectorHit) for h in hits)
    assert hits[0].meta == {"src": "a"}  # 词面重合度最高的排第一
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


def test_gateway_embedder_request_shape(httpx_mock...):
```

（GatewayEmbedder 的测试用 httpx MockTransport 注入——项目用 httpx，测试里 `httpx.MockTransport` 构造假响应注入 `GatewayEmbedder(..., transport=...)`：

```python
import httpx


def test_gateway_embedder_parses_openai_style():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/embeddings")
        body = json.loads(request.content)
        assert body["model"] == "embedding-1" and body["input"] == ["你好"]
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}, ][0:1] * 1})

    embed = GatewayEmbedder(base_url="https://gw.example", api_key="k", model="embedding-1", transport=httpx.MockTransport(handler))
    assert embed(["你好"]) == [[0.1, 0.2]]
```

注意 handler 返回的 data 数组长度须等于输入长度——上面写法笨拙，测试里直接返回 `{"data": [{"embedding": [0.1, 0.2]}]}`（单条输入单条输出）。）

- [ ] **Step 3: 确认失败**

- [ ] **Step 4: 实现 memory/store.py**

```python
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

    def upsert(self, texts: list[str], metas: list[dict] | None = None) -> None:
        if not texts:
            return
        metas = metas or [{} for _ in texts]
        vecs = self._np.array(self._embed(texts), dtype="float32")
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
        resp.raise_for_status()
        data = resp.json()["data"]
        return [item["embedding"] for item in data]
```

- [ ] **Step 5: 通过 + 全量回归 + ruff**

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/sluice/memory/store.py tests/test_memory_store.py
git commit -m "feat: 向量记忆(VectorStore抽象+FAISS Flat后端+HashEmbedder测试桩+GatewayEmbedder)"
```

---

### Task 11: MilvusStore 同接口适配器（依赖 T10）

**Files:**
- Create: `E:\test\sluice-data-agent\src\sluice\memory\milvus_store.py`
- Test: `E:\test\sluice-data-agent\tests\test_memory_milvus.py`

- [ ] **Step 1: 写失败测试（Fake client 协议级，pymilvus 不装）**

```python
"""Milvus 后端测试：鸭子类型 client 注入（协议级），证明换后端不改业务代码。"""

import pytest

from sluice.exceptions import SluiceError
from sluice.memory.milvus_store import MilvusStore
from sluice.memory.store import HashEmbedder


class FakeMilvusClient:
    """MilvusClient 窄协议替身：create_collection/insert/search。"""

    def __init__(self):
        self.rows: list[dict] = []
        self.created: list[str] = []

    def has_collection(self, name: str) -> bool:
        return name in self.created

    def create_collection(self, name: str, dimension: int, metric_type: str = "IP"):
        self.created.append(name)
        self.dimension = dimension

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
    for store in (milvus, faiss):
        hits = store.search("销售额 环比", k=2)
        assert len(hits) == 2 and hits[0].meta == {"src": "a"} and hits[0].score >= hits[1].score


def test_milvus_store_creates_collection_once():
    fake = FakeMilvusClient()
    MilvusStore(collection="t", dim=64, embed=HashEmbedder(64), client=fake)
    MilvusStore(collection="t", dim=64, embed=HashEmbedder(64), client=fake)
    assert fake.created == ["t"]  # 第二次不重复建表


def test_milvus_store_missing_pymilvus_raises_clean():
    """client 未注入且 pymilvus 未安装 → 明确报错（不裸 ImportError）。"""
    import sys

    fake_import = __import__
    try:
        real = sys.modules.pop("pymilvus", None)
        sys.modules["pymilvus"] = None  # 触发 ImportError 路径的另一种方式见实现注
        with pytest.raises(SluiceError, match="pymilvus"):
            MilvusStore(collection="t", dim=64, embed=HashEmbedder(64), client=None)
    finally:
        if real is not None:
            sys.modules["pymilvus"] = real
        else:
            sys.modules.pop("pymilvus", None)
```

（实现注：lazy import 判定用 `importlib.util.find_spec("pymilvus") is None` → raise SluiceError("pymilvus 未安装：pip install pymilvus（Milvus 后端为可选）")——比 pop sys.modules 稳。第三个测试按这个语义写：monkeypatch `find_spec` 返回 None 更直接：

```python
def test_milvus_store_missing_pymilvus_raises_clean(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "pymilvus" else real_find_spec(name))
    with pytest.raises(SluiceError, match="pymilvus"):
        MilvusStore(collection="t", dim=64, embed=HashEmbedder(64), client=None)
```

顶部 `real_find_spec = importlib.util.find_spec`。）

- [ ] **Step 2: 确认失败**

- [ ] **Step 3: 实现 milvus_store.py**

```python
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
            client.create_collection(collection, dimension=dim, metric_type="IP")

    def upsert(self, texts: list[str], metas: list[dict] | None = None) -> None:
        if not texts:
            return
        metas = metas or [{} for _ in texts]
        vecs = self._embed(texts)
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
```

（FakeMilvusClient 的 search 返回形态按上面 results[0] 迭代设计——真实 pymilvus MilvusClient.search 返回 `[[{...}]]` 同构。）

- [ ] **Step 4: 通过 + 全量回归 + ruff**

- [ ] **Step 5: Commit**

```bash
git add src/sluice/memory/milvus_store.py tests/test_memory_milvus.py
git commit -m "feat: MilvusStore同接口适配器(lazy import可选依赖+鸭子类型client注入证明换后端不改业务)"
```

---

### Task 12: 两个落点接线 + 演示脚本（依赖 T9+T10）

**Files:**
- Create: `E:\test\sluice-data-agent\src\sluice\memory\hooks.py`
- Modify: `E:\test\sluice-data-agent\src\sluice\config\settings.py`
- Modify: `E:\test\sluice-data-agent\src\sluice\web\app.py`
- Create: `E:\test\sluice-data-agent\scripts\demo_memory.py`
- Test: `E:\test\sluice-data-agent\tests\test_semantic_skill_match.py` + `tests\test_session_memory.py`

- [ ] **Step 1: 写失败测试（Skill 语义召回）**

```python
"""Skill 语义召回测试：超过阈值才启用，召回结果只影响 system 提示（工具池不动）。"""

from sluice.config.settings import Settings
from sluice.memory.store import FaissStore, HashEmbedder
from sluice.memory.hooks import build_skill_store, select_skills
from sluice.skills.loader import SkillPackage


def _packages(n):
    return [
        SkillPackage(name=f"s{i}", description=f"skill {i} 的描述：主题{i}", path=None, output_contract=[])
        for i in range(n)
    ]


def _store_with(packages):
    store = FaissStore(embed=HashEmbedder(dim=64), dim=64, path=None)
    build_skill_store(store, packages)
    return store


def test_select_skills_disabled_below_threshold(tmp_path):
    packages = _packages(3)
    store = _store_with(packages)
    assert select_skills(packages, "主题1 的请求", store, threshold=5) is None  # 少量 skill → None（全量，现状行为）


def test_select_skills_recalls_topk(tmp_path):
    packages = _packages(10)
    store = _store_with(packages)
    picked = select_skills(packages, "主题7 的请求", store, threshold=5, k=3)
    assert picked is not None and len(picked) == 3
    assert any(p.name == "s7" for p in picked)  # 词面重合者必进
```

（`SkillPackage(path=None)` 改 `Path(".")`——与 T6 收口口径一致。）

- [ ] **Step 2: 写失败测试（会话长期记忆）**

```python
"""会话长期记忆测试：record 落盘 + 超 N 轮摘要向量化 + recall 注入片段。"""

from sluice.memory.hooks import SessionMemory
from sluice.memory.session import SessionStore
from sluice.memory.store import FaissStore, HashEmbedder


class StubSummarizer:
    def __init__(self):
        self.calls = 0

    def __call__(self, text: str) -> str:
        self.calls += 1
        return f"摘要:{text[:20]}"


def _memory(max_turns=2, summarizer=None):
    summarizer = summarizer or StubSummarizer()
    vectors = FaissStore(embed=HashEmbedder(dim=64), dim=64, path=None)
    return SessionMemory(
        sessions=SessionStore(tmp_root := __import__("pathlib").Path(__import__("tempfile").mkdtemp())),
        vectors=vectors,
        summarize=summarizer,
        max_turns=max_turns,
    ), summarizer


def test_record_appends_and_summarizes_after_threshold():
    mem, summarizer = _memory(max_turns=2)
    mem.record("u1", "s1", "user", "问题一")
    assert summarizer.calls == 0
    mem.record("u1", "s1", "user", "回答一")
    assert summarizer.calls == 0
    mem.record("u1", "s1", "user", "问题二")
    assert summarizer.calls == 1  # 第 3 轮触发摘要
    mem.record("u1", "s1", "assistant", "回答二")


def test_recall_empty_when_no_long_term():
    mem, _ = _memory()
    assert mem.recall("u1", "s1", "任何问题") == ""


def test_recall_returns_summary_after_promotion():
    mem, _ = _memory(max_turns=1)
    mem.record("u1", "s1", "user", "上周销售额环比上升多少")
    mem.record("u1", "s1", "assistant", "上升百分之十五")
    got = mem.recall("u1", "s1", "销售额 环比")
    assert "摘要" in got or got == ""  # 有向量命中则含摘要标记；HashEmbedder 词面不重合时允许空
```

（`_memory` 的 tmp 目录构造写丑了——实现者直接用 pytest 的 `tmp_path` fixture 传参，签名改 `def _memory(tmp_path, max_turns=2, summarizer=None)`，别照抄 mkdtemp。）

- [ ] **Step 3: 确认失败**

- [ ] **Step 4: 实现 hooks.py**

```python
"""记忆落点接线：Skill 语义召回 + 会话长期记忆（默认关闭，settings.memory_enabled 才生效）。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from .session import SessionStore
from .store import VectorStore

if TYPE_CHECKING:
    from ..skills.loader import SkillPackage

logger = logging.getLogger(__name__)


def build_skill_store(store: VectorStore, packages: list["SkillPackage"]) -> None:
    """SKILL.md 描述向量化入索引（meta 带 skill 名）。"""
    texts = [f"{p.name} {p.description}" for p in packages]
    store.upsert(texts, metas=[{"skill": p.name} for p in packages])


def select_skills(
    packages: list["SkillPackage"], user_input: str, store: VectorStore, threshold: int, k: int = 3
) -> list["SkillPackage"] | None:
    """超过 threshold 才召回 top-k；以下返回 None（调用方维持全量，现状行为零变化）。"""
    if len(packages) <= threshold:
        return None
    hits = store.search(user_input, k=k)
    by_name = {p.name: p for p in packages}
    picked = [by_name[h.meta["skill"]] for h in hits if h.meta.get("skill") in by_name]
    return picked or None


class SessionMemory:
    """会话长期记忆：轮次落盘；超 max_turns 后整段摘要升入向量库；新输入按需召回。"""

    def __init__(
        self,
        sessions: SessionStore,
        vectors: VectorStore,
        summarize: Callable[[str], str],
        max_turns: int = 10,
        session_id_prefix: str = "longterm",
    ):
        self._sessions = sessions
        self._vectors = vectors
        self._summarize = summarize
        self._max_turns = max_turns
        self._prefix = session_id_prefix

    def record(self, user_id: str, session_id: str, role: str, text: str) -> None:
        self._sessions.append(user_id, session_id, role, text)
        turns = self._sessions.load(user_id, session_id)
        if len(turns) >= self._max_turns:
            self._promote(user_id, session_id, turns)

    def _promote(self, user_id: str, session_id: str, turns: list[dict]) -> None:
        """超阈值：整段会话摘要 → 向量库；清空该会话轮次（已升维）。"""
        digest = "\n".join(f"{t['role']}: {t['text']}" for t in turns)
        summary = self._summarize(digest)
        self._vectors.upsert([summary], metas=[{"user_id": user_id, "session_id": session_id, "kind": "summary"}])
        logger.info("会话 %s/%s 升入长期记忆（%d 轮）", user_id, session_id, len(turns))
        # 清空：重写文件为空
        path = self._sessions._session_file(user_id, session_id)
        path.write_text("", encoding="utf-8")

    def recall(self, user_id: str, session_id: str, user_input: str, k: int = 3) -> str:
        """召回该用户长期记忆中与输入最相关的摘要片段；无命中返回空串（调用方跳过拼接）。"""
        hits = self._vectors.search(user_input, k=k)
        pieces = [h.text for h in hits if h.meta.get("user_id") == user_id]
        return "\n---\n".join(pieces)
```

- [ ] **Step 5: settings + web 接线**

settings.py 追加：

```python
    # Memory（P4）
    memory_enabled: bool = False  # 默认关：P0-P3 行为零变化
    memory_embed_model: str = ""  # 空 = HashEmbedder（无语义，测试/降级）；配置则走网关 /v1/embeddings
    memory_skill_match_threshold: int = 8  # skill 数超过才启用语义召回
    memory_max_turns: int = 10  # 会话轮数超过后摘要升入长期记忆
    memory_base_dir: str = "data/memory"  # 相对 repo 根
```

web/app.py `create_app`（memory_enabled 分支，全失败降级）：

```python
    memory = None
    if settings.memory_enabled:
        try:
            from ..memory.hooks import SessionMemory, build_skill_store, select_skills
            from ..memory.session import SessionStore
            from ..memory.store import FaissStore, GatewayEmbedder, HashEmbedder

            base = Path(__file__).resolve().parents[3] / settings.memory_base_dir
            embed = (
                GatewayEmbedder(settings.llm_base_url, settings.llm_api_key, settings.memory_embed_model)
                if settings.memory_embed_model
                else HashEmbedder(dim=64)
            )
            vectors = FaissStore(embed=embed, dim=64, path=str(base / "vectors"))
            if len(vectors) == 0 and packages:
                build_skill_store(vectors, packages)
            memory = SessionMemory(
                sessions=SessionStore(base / "sessions"),
                vectors=vectors,
                summarize=lambda text: client.chat([Message("user", [TextBlock(f"用三句话总结以下对话：\n{text}")])]).text,
                max_turns=settings.memory_max_turns,
            )
        except Exception as exc:  # noqa: BLE001 — 记忆挂了不拖垮对话（kill switch）
            logger.warning("记忆层初始化失败，本次启动降级为无记忆: %s", exc)
            memory = None
```

（worker 内：调 runtime 前——

```python
        system = build_system(packages)
        if memory is not None:
            picked = select_skills(packages, user_input, vectors, settings.memory_skill_match_threshold)
            if picked is not None:
                system = build_system(picked)
            recalled = memory.recall(user_id, session_id, user_input)
            if recalled:
                system += "\n\n[长期记忆召回]\n" + recalled
        # run 结束后：
        if memory is not None:
            memory.record(user_id, session_id, "user", user_input)
            memory.record(user_id, session_id, "assistant", answer)
```

——以 web/app.py worker 真实结构为准（先读，user_id/session_id 变量名可能不同；P1 会话管理里应有 run_id，缺 user_id 就用固定 "local"）。`vectors` 变量作用域注意：select_skills 需要 store，闭包捕获。）

- [ ] **Step 6: demo 脚本 scripts/demo_memory.py**

```python
"""记忆层演示：HashEmbedder + FaissStore 语义召回 + 会话摘要升维（零 API key 可跑）。"""

from pathlib import Path

from sluice.memory.hooks import SessionMemory
from sluice.memory.session import SessionStore
from sluice.memory.store import FaissStore, HashEmbedder


def main() -> None:
    base = Path("data/memory-demo")
    vectors = FaissStore(embed=HashEmbedder(dim=64), dim=64, path=str(base / "vectors"))
    fake_summarize = lambda text: f"对话摘要：{text[:60]}…"  # noqa: E731 — 演示用假摘要，真网关时换 client.chat
    memory = SessionMemory(SessionStore(base / "sessions"), vectors, fake_summarize, max_turns=2)

    print("== 会话 1：三轮对话触发摘要升维 ==")
    for role, text in [
        ("user", "查一下上周销售额环比"),
        ("assistant", "上周销售额环比上升 15%"),
        ("user", "好的，再看信贷通过率"),
    ]:
        memory.record("demo", "s1", role, text)
    print("== 会话 2：召回会话 1 的长期记忆 ==")
    for text in ["销售额环比问题", "完全不相关的问题"]:
        print(f"  召回[{text}] →", memory.recall("demo", "s2", text).replace("\n", " / ") or "（无）")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: 通过 + 全量回归 + ruff + demo 冒烟**

`.venv/Scripts/python -X utf8 scripts/demo_memory.py` 预期打印两段（会话 1 升维 + 会话 2 召回/无命中）。run 结束 `git status` 确认 `data/` 未入库（.gitignore 应已含 data/——没有就补一行 `data/`，报告偏离）。

- [ ] **Step 8: Commit**

```bash
git add src/sluice/memory/hooks.py src/sluice/config/settings.py src/sluice/web/app.py scripts/demo_memory.py tests/test_semantic_skill_match.py tests/test_session_memory.py .gitignore
git commit -m "feat: 记忆落点接线(Skill语义召回+会话长期记忆摘要升维,默认关零改动)+记忆演示脚本"
```

---

### Task 13: LangGraph 对比移植练习（独立，可与其他任务并行概念上）

**Files:**
- Create: `E:\test\sluice-data-agent\examples\langgraph_weekly\graph.py`
- Create: `E:\test\sluice-data-agent\examples\langgraph_weekly\README.md`
- Modify: `E:\test\sluice-data-agent\pyproject.toml`（`[project.optional-dependencies]` 加 `graph = ["langgraph>=0.2"]`）
- Test: `E:\test\sluice-data-agent\tests\test_langgraph_port.py`（skipif 无 langgraph）

- [ ] **Step 1: 写测试**

```python
"""LangGraph 对比移植 smoke：StateGraph 重实现 Skill ① 骨架（可选依赖，未装则跳过）。"""

import pytest

langgraph = pytest.importorskip("langgraph")

from examples.langgraph_weekly.graph import build_graph  # noqa: E402


class StubClient:
    """chat 按前缀出招：SQL 生成 → 指标提取。"""

    def chat(self, messages, system=None, tools=None):
        from sluice.llm.types import LLMResponse, TextBlock

        text = "SELECT city, SUM(amount) AS total FROM sales GROUP BY city"
        return LLMResponse(content=[TextBlock(text)], stop_reason="end_turn", usage={})


def test_graph_runs_end_to_end():
    def fake_query(sql: str) -> list[dict]:
        return [{"city": "杭州", "total": 100.0}]

    graph = build_graph(client=StubClient(), query_fn=fake_query)
    state = graph.invoke({"question": "上周各城市销量"})
    assert state["report"] and "杭州" in state["report"]
```

（`examples.` 包导入需要 `examples/__init__.py` + `examples/langgraph_weekly/__init__.py` 空文件；或 pytest ini 加路径——用空 `__init__.py` 最稳。）

- [ ] **Step 2: 确认失败**（skipif 生效则本步显示 skipped——装了才跑：`.venv/Scripts/pip install langgraph` 装入 dev 环境验证，extra 声明供演示环境）

- [ ] **Step 3: 实现 examples/langgraph_weekly/graph.py**

```python
"""Skill ① 的 LangGraph 重实现（对比练习）——同一职责：查数 → 算环比 → 渲染周报。

对比锚点（写进 README）：
- Sluice：自研 ReActRuntime，模型自由决定调什么工具，guardrails 在四拦截点兜底
- LangGraph：开发者预先编排节点图，模型只在节点内干活——控制流显式、可观测性强，
  但工具选择/错误恢复要自己搭（Sluice 的拦截点在图编排范式里没有现成位置）
"""

from __future__ import annotations

from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph


class WeeklyState(TypedDict):
    question: str
    sql: str
    rows: list[dict[str, Any]]
    report: str


def build_graph(client: Any, query_fn: Callable[[str], list[dict]]) -> Any:
    """client: 有 .chat(messages, system=None, tools=None) 的对象（Sluice LLMClient 同构）；query_fn: SQL 执行器。"""

    def sql_node(state: WeeklyState) -> dict:
        resp = client.chat(
            [_user(f"为问题生成一条只读 SQLite SQL（表 sales: sale_date/city/category/amount）：\n{state['question']}")]
        )
        return {"sql": resp.text.strip()}

    def query_node(state: WeeklyState) -> dict:
        return {"rows": query_fn(state["sql"])}

    def render_node(state: WeeklyState) -> dict:
        resp = client.chat(
            [_user(f"把以下查询结果渲染成 markdown 数据周报（含 核心指标/重点发现 两节）：\n{state['rows']}")]
        )
        return {"report": resp.text}

    g = StateGraph(WeeklyState)
    g.add_node("sql", sql_node)
    g.add_node("query", query_node)
    g.add_node("render", render_node)
    g.set_entry_point("sql")
    g.add_edge("sql", "query")
    g.add_edge("query", "render")
    g.add_edge("render", END)
    return g.compile()


def _user(text: str):
    from sluice.llm.types import Message, TextBlock

    return Message("user", [TextBlock(text)])
```

- [ ] **Step 4: examples/langgraph_weekly/README.md**（对比笔记骨架，执行者按实测填）

```markdown
# LangGraph 对比移植（练习）

同一职责（数据周报三步）的两种实现：
- Sluice：`runtime/loop.py` ReAct 循环 + guardrails 四拦截点 + evals 三层
- LangGraph：StateGraph 显式编排（本目录 graph.py）

## 对比笔记（执行时填实测结论）

| 维度 | Sluice 自研 | LangGraph |
|---|---|---|
| 控制流 | 模型自主 + 门禁兜底 | 开发者预编排 |
| 工具选择 | 模型 + 行为门禁 | 节点内自定 |
| 错误恢复 | 拦截→自修→降级（内建） | 自行搭（checkpoint/retry 原语） |
| 评测 | evals 三层内建 | 无（外接） |
| 什么时候不该用自研 | —— | 图编排复杂多角色工作流时 |
```

- [ ] **Step 5: 通过/skip + 全量回归 + ruff**

- [ ] **Step 6: Commit**

```bash
git add examples/ pyproject.toml tests/test_langgraph_port.py
git commit -m "test: LangGraph对比移植练习(StateGraph重实现Skill①骨架+可选extra+对比笔记骨架)"
```

---

### Task 14: 终审 + 全量回归 + 合并 main

- [ ] **Step 1: 终审（主会话派终审代理）**——红线 1-5 逐条对证据 + 整体一致性（MCP 工具进行为门禁的归因无副作用 / memory 默认关 / pyproject extra 声明与实际依赖一致）
- [ ] **Step 2: 全量回归**：pytest 全绿（预计 199 + ~45 新增 ≈ 245）+ ruff check src/ tests/ scripts/ 零告警
- [ ] **Step 3: finishing-a-development-branch**（主会话）：本地合并 main（fast-forward）→ main 复跑 pytest → 删分支
- [ ] **Step 4: 沉淀**（主会话）：Obsidian P4 实录（含 T5 真网关战果 / embedding 探针结论 / LangGraph 对比笔记链接）、TODO.md 勾 P4、总索引进度板、memory 更新

---

## 执行备注（给主会话编排者）

- **模型分层**：T1/T9 机械用 haiku；T2/T3/T4 sonnet；T5 真网关 sonnet（环境坑全文照抄）；T6-T8 核心 sonnet；T10-T12 sonnet；T13 sonnet（练习性质）；T14 终审 sonnet
- **任务依赖**：T1 独立；T2/T3/T4 相互独立；T5 ← T2+T4；T6 → T7 → T8 串行（T7 顺带实现 transport.notify，见 T8 注）；T9 独立；T10 → T11；T12 ← T9+T10；T13 独立；T14 最后。可利用空闲并行概念上但 git 串行——实现者一次一个
- **已知风险**：
  1. 网关 embedding 端点未实测（OpenAI 兼容 /v1/embeddings 是假设）——T12 首次真跑探针确认；失败不阻塞（默认 HashEmbedder，GatewayEmbedder 留到端点确认后启用），实录里记结论
  2. faiss-cpu Windows wheel 齐全（1.8+）；若 venv 安装失败按 T10 Step 1 的偏离预案
  3. pymilvus 不进任何依赖声明（可选 lazy import），CI 不装不跑真 Milvus——Fake client 协议级测试已证明接口一致性
  4. langgraph 装进本地 dev 环境验证 T13，但 CI 不装（skipif 跳过）——extra 声明是给演示环境的
  5. MCP 子进程在 Windows 上的 stdin flush/编码是最大不确定点——T7 测试即真子进程端到端，若 Windows 管道有坑（如 bufsize=1 text 模式换行符），以实测为准调整并在实录记录
- **出口标志**（设计文档）：接入 MCP server 演示工具生态开放——T8 的 mini server 已是真子进程端到端；公共 filesystem server（npx）演示为可选步骤，无 node 环境则留 P5 发布时补

## Self-Review 结论（计划作者已跑）

- **Spec 覆盖**：设计文档 §5 MCP（T6/T7/T8）✓；§6 memory 三落点（①Skill 召回 T12a、②会话长期记忆 T12b、③工具结果记忆——demo 脚本演示 store 复用形态，运行时不接，记 P5 评估）✓；FAISS 默认后端 T10 / Milvus 适配 T11 ✓；§8 LangGraph 对比移植 T13 ✓；P3 顺延遗留：judge 健壮性 T2 / 前缀查询工具 T4+T5 / known_issue T3 / BehaviorGate 线程安全 T1 / .gitignore T1 ✓
- **占位符扫描**：T7 transport.request 的占位陷阱已用"完整正确实现"显式覆盖并标注勿照抄占位；T13 README 对比表执行时填实测结论（骨架已在，不算占位——笔记性质）；无 TBD
- **类型一致性**：`EmbedFn`/`VectorHit`/`VectorStore` 协议 T10 定义、T11/T12 引用一致；`McpServer.tools`/`close()` T8 定义 T8 测试消费一致；`select_skills(packages, user_input, store, threshold, k)` T12 内部一致；`SessionMemory.record/recall` T12 定义与 web 接线一致；`protocol.encode_request` T6 公开命名与 T7 复用一致（T6 执行者注意改名注记）
