# P2 可靠性实现计划（Guardrails 四拦截点 + 审计可视化 + 恢复策略 + Skill ② 跨域）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LLM 输出进执行路径前过确定性校验——幻觉工具/幻觉表/坏参数拦截、重复调用打断、token 预算、产出契约校验 + 定向自修一轮，全部拦截/自修产生结构化事件进 SSE 轨迹流并落审计副本；新增跨域 Skill ② 信贷周报。

**Architecture:** 新增 `src/sluice/guardrails/`（base/input_gate/behavior_gate/output_gate/chain 五模块），`GuardrailChain` 以可选参数接入 `ReActRuntime`（None = kill switch 关闭，行为回到 P1）。拦截决策只做确定性判断（在册/合法/齐全），语义质量判断留给 P3 evals。事件契约 `{"type": "guardrail", ...}` 与 ReAct 事件流同构，SSE/前端零协议改动透传。

**Tech Stack:** Python 3.10+ / pytest / Flask SSE（已有）/ React 19 + Vite（已有）。零新依赖（schema 校验与 YAML frontmatter 手写极简解析）。

**基线（2026-10-19，main @ f04176b）**：71 tests 全绿；事件契约见 `runtime/loop.py` 模块 docstring；`Settings` env 前缀 `SLUICE_`；测试一律用 `.venv/Scripts/python -m pytest` / `.venv/Scripts/python -m ruff`（全局工具会假绿）。

---

## 文件结构总览

| 文件 | 职责 |
|------|------|
| `src/sluice/guardrails/__init__.py` | 空 |
| `src/sluice/guardrails/base.py` | 拦截事件契约 `guardrail_event()` + 审计落盘 `AuditLog`（JSONL） |
| `src/sluice/guardrails/input_gate.py` | 拦截点①工具调用前：幻觉工具 / 参数 schema / SQL 幻觉表 |
| `src/sluice/guardrails/behavior_gate.py` | 拦截点②循环运行中：连续重复调用打断 + token 预算 |
| `src/sluice/guardrails/output_gate.py` | 拦截点③产出交付前：非空 + 必需小节 |
| `src/sluice/guardrails/chain.py` | `GuardrailChain`：编排四拦截点 + run 级会话 + 产出契约登记 |
| `src/sluice/exceptions.py` | + `BudgetExceededError` |
| `src/sluice/runtime/loop.py` | run() 接入门禁 + 拦截点④恢复策略（自修一轮/降级交付） |
| `src/sluice/config/settings.py` | + `guardrails_enabled` / `react_token_budget` / `audit_dir` |
| `src/sluice/tools/base.py` | + `owner_skill` 元数据 |
| `src/sluice/tools/query_data.py` | + `table_allowlist()`（幻觉表校验的在册名单） |
| `src/sluice/skills/loader.py` | frontmatter + `output_contract`；工具打 `owner_skill` |
| `src/sluice/web/app.py` | create_app 组装 GuardrailChain |
| `src/sluice/cli/main.py` | `_run` 组装 GuardrailChain |
| `web/frontend/src/App.jsx` | guardrail 事件卡片渲染（拦截琥珀/修复绿/降级红） |
| `scripts/seed_demo_db.py` | + `credit_applications` 信贷表 |
| `skills/weekly-report/SKILL.md` | frontmatter + `output_contract` |
| `skills/credit-weekly/` | Skill ② 跨域信贷周报（SKILL.md/tools.py/config.yaml） |
| `scripts/demo_guardrails.py` | 确定性现场演示脚本（不依赖网关） |
| `.gitignore` | + `audit/` |

**明确不做（YAGNI / 留给后续阶段）**：run 级按意图的 Skill 权限收窄（需语义匹配，P4）；单轮输出长度腰斩检测（判据模糊，等 evals 有基线再做）；审计可视化面板（JSONL 落盘即产品能力，前端 P2 只做轨迹内事件卡片）；`sluice eval` 子命令（P3）。

---

### Task 1: 事件契约 + 审计落盘

**Files:**
- Create: `src/sluice/guardrails/__init__.py`（空文件）
- Create: `src/sluice/guardrails/base.py`
- Test: `tests/test_guardrails_base.py`

- [ ] **Step 1: 写失败测试**

```python
"""tests/test_guardrails_base.py"""
import json

from sluice.guardrails.base import AuditLog, guardrail_event


def test_guardrail_event_shape():
    ev = guardrail_event("input", "blocked", "幻觉工具", 3, tool="no_such_tool")
    assert ev == {
        "type": "guardrail",
        "gate": "input",
        "action": "blocked",
        "round": 3,
        "reason": "幻觉工具",
        "detail": {"tool": "no_such_tool"},
    }
    json.dumps(ev, ensure_ascii=False)  # 必须可 JSON 序列化（SSE 透传）


def test_guardrail_event_without_detail():
    ev = guardrail_event("output", "repaired", "自修通过", 4)
    assert "detail" not in ev


def test_audit_log_appends_jsonl(tmp_path):
    log = AuditLog(tmp_path / "audit")
    log.append({"kind": "input_blocked", "tool": "x"})
    log.append({"kind": "output_rejected", "answer": "原回答"})
    lines = (tmp_path / "audit" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["kind"] == "input_blocked" and "ts" in first
    assert json.loads(lines[1])["answer"] == "原回答"


def test_audit_log_lazy_dir_creation(tmp_path):
    """构造不建目录，首次 append 才落盘——kill switch 场景不产生副作用。"""
    log = AuditLog(tmp_path / "never")
    assert not (tmp_path / "never").exists()
    log.append({"kind": "x"})
    assert (tmp_path / "never" / "audit.jsonl").is_file()
```

- [ ] **Step 2: 跑测试确认失败** — `.venv/Scripts/python -m pytest tests/test_guardrails_base.py -v`，FAIL `ModuleNotFoundError: sluice.guardrails`
- [ ] **Step 3: 实现** — `src/sluice/guardrails/__init__.py` 空文件；`src/sluice/guardrails/base.py`：

```python
"""Guardrails 基础件：拦截事件契约 + 审计落盘。

设计原则（设计文档 §4）：LLM 输出一律不可信，进执行路径前先过确定性校验；
guardrails 只做确定性判断（在册/合法/齐全），不做语义质量判断——那是 evals 的事。
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def guardrail_event(gate: str, action: str, reason: str, round_no: int, **detail) -> dict:
    """拦截事件：与 ReAct 事件流同构（type=guardrail），SSE 原样透传给前端。

    gate: input | behavior | output；action: blocked | repaired | degraded。
    passed 不产事件（每个工具调用都发会淹没轨迹，拦截才可见）。
    """
    event = {
        "type": "guardrail",
        "gate": gate,
        "action": action,
        "round": round_no,
        "reason": reason,
    }
    if detail:
        event["detail"] = detail
    return event


class AuditLog:
    """被拒/被改产出的审计副本（JSONL 追加写，一行一条）。"""

    def __init__(self, audit_dir: str | Path):
        self._path = Path(audit_dir) / "audit.jsonl"

    def append(self, entry: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: 跑测试通过** — 同 Step 2 命令，4 passed
- [ ] **Step 5: Commit** — `git add src/sluice/guardrails tests/test_guardrails_base.py && git commit -m "feat: guardrails事件契约+审计JSONL落盘"`

---

### Task 2: 输入门禁（幻觉工具 / 参数 schema / SQL 幻觉表）

**Files:**
- Create: `src/sluice/guardrails/input_gate.py`
- Modify: `src/sluice/tools/query_data.py`（+ `table_allowlist()`）
- Test: `tests/test_input_gate.py`

- [ ] **Step 1: 写失败测试**

```python
"""tests/test_input_gate.py"""
import sqlite3

import pytest

from sluice.guardrails.input_gate import InputGate, extract_sql_tables, validate_schema
from sluice.llm.types import ToolUseBlock
from sluice.tools.base import Tool, ToolResult


class SqlTool(Tool):
    name = "stub"
    description = "stub"
    parameters = {
        "type": "object",
        "properties": {"sql": {"type": "string"}},
        "required": ["sql"],
    }

    def execute(self, args):
        return ToolResult("ok")


def test_validate_schema_missing_required():
    assert "缺少必填参数: sql" in validate_schema({}, SqlTool.parameters)


def test_validate_schema_wrong_type():
    assert "类型应为 string" in validate_schema({"sql": 123}, SqlTool.parameters)


def test_validate_schema_bool_not_integer():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    assert "类型应为 integer" in validate_schema({"n": True}, schema)


def test_validate_schema_enum_violation():
    schema = {"type": "object", "properties": {"c": {"type": "string", "enum": ["a", "b"]}}}
    assert "不在允许范围" in validate_schema({"c": "z"}, schema)


def test_validate_schema_pass():
    assert validate_schema({"sql": "select 1"}, SqlTool.parameters) is None


def test_extract_sql_tables_basic():
    assert extract_sql_tables("SELECT * FROM sales s JOIN cities c ON s.c = c.id") == {
        "sales",
        "cities",
    }


def test_extract_sql_tables_cte_and_quoting():
    sql = 'WITH t AS (SELECT 1) SELECT * FROM `main`.`t` JOIN "dim_date"'
    assert extract_sql_tables(sql) == {"t", "dim_date"}


def _ghost_db(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("create table sales (id integer)")
    conn.commit()
    conn.close()
    return str(db)


def test_input_gate_blocks_hallucinated_tool():
    verdict = InputGate().check(ToolUseBlock("t1", "no_such_tool", {}), None)
    assert verdict is not None and "幻觉工具" in verdict[0] and verdict[1]["tool"] == "no_such_tool"


def test_input_gate_blocks_schema_violation():
    verdict = InputGate().check(ToolUseBlock("t1", "stub", {"sql": 1}), SqlTool())
    assert verdict is not None and "参数校验未通过" in verdict[0]


def test_input_gate_blocks_hallucinated_table(tmp_path):
    tool = SqlTool()
    tool.table_allowlist = lambda: ["sales"]  # noqa: B010 — 测试桩，模拟 QueryDataTool 的在册名单
    verdict = InputGate().check(ToolUseBlock("t1", "stub", {"sql": "select * from ghost"}), tool)
    assert verdict is not None and "幻觉表" in verdict[0]
    assert verdict[1]["unknown_tables"] == ["ghost"]


def test_input_gate_allows_cte_self_reference(tmp_path):
    tool = SqlTool()
    tool.table_allowlist = lambda: ["sales"]  # noqa: B010
    sql = "WITH t AS (SELECT id FROM sales) SELECT * FROM t"
    assert InputGate().check(ToolUseBlock("t1", "stub", {"sql": sql}), tool) is None


def test_input_gate_passes_valid_call():
    assert InputGate().check(ToolUseBlock("t1", "stub", {"sql": "select 1"}), SqlTool()) is None
```

- [ ] **Step 2: 跑测试确认失败** — `.venv/Scripts/python -m pytest tests/test_input_gate.py -v`，FAIL `ModuleNotFoundError`
- [ ] **Step 3: 实现** — `src/sluice/guardrails/input_gate.py`：

```python
"""输入门禁（拦截点①，工具调用前）：幻觉工具拦截 / 参数 schema 校验 / SQL 表名在册校验。"""

from __future__ import annotations

import re

from ..llm.types import ToolUseBlock
from ..tools.base import Tool

_SQL_TABLE_RE = re.compile(r"\b(?:from|join)\s+[`\"']?([a-zA-Z_][\w.]*)", re.IGNORECASE)
_CTE_RE = re.compile(r"\b(\w+)\s+as\s*\(", re.IGNORECASE)

_TYPE_CHECKS = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def validate_schema(args: dict, parameters: dict) -> str | None:
    """极简 JSON Schema 校验（平台用到的子集：type/required/properties/enum）。

    返回 None 放行；否则返回可直接回喂模型的自纠提示。
    未声明的参数放过，由工具自行处理——门禁只拦确定性错误。
    """
    if not isinstance(args, dict):
        return f"参数必须是对象，收到 {type(args).__name__}"
    props = parameters.get("properties", {})
    for req in parameters.get("required", []):
        if req not in args:
            return f"缺少必填参数: {req}"
    for key, value in args.items():
        spec = props.get(key)
        if spec is None:
            continue
        expected = spec.get("type")
        if expected:
            # bool 是 int 子类，数字类型须显式排除
            ok = isinstance(value, _TYPE_CHECKS[expected]) and not (
                isinstance(value, bool) and expected in ("integer", "number")
            )
            if not ok:
                return f"参数 {key} 类型应为 {expected}，收到 {type(value).__name__}"
        if "enum" in spec and value not in spec["enum"]:
            return f"参数 {key} 取值 {value!r} 不在允许范围 {spec['enum']}"
    return None


def extract_sql_tables(sql: str) -> set[str]:
    """提取 SQL 引用的表名（FROM/JOIN 后标识符，去 schema/引号前缀）。

    CTE 别名（``x as (``）一并计入——WITH 自引用是合法读法，不能误报幻觉表。
    方向性设计：宁可多放（别名误入白名单），不可错拦合法查询。
    """
    tables = {m.split(".")[-1].lower() for m in _SQL_TABLE_RE.findall(sql)}
    aliases = {m.lower() for m in _CTE_RE.findall(sql)}
    return tables | aliases


class InputGate:
    """check 返回 None 放行；(reason, detail) 表示拦截，reason 可直接回喂模型。"""

    def check(self, tu: ToolUseBlock, tool: Tool | None) -> tuple[str, dict] | None:
        if tool is None:
            return (f"工具 {tu.name} 不在注册表中（幻觉工具），调用被拦截", {"tool": tu.name})
        reason = validate_schema(tu.input, tool.parameters)
        if reason:
            return (f"参数校验未通过：{reason}", {"tool": tu.name, "args": tu.input})
        allowlist = getattr(tool, "table_allowlist", None)
        if callable(allowlist):
            sql = tu.input.get("sql", "") if isinstance(tu.input, dict) else ""
            unknown = extract_sql_tables(sql) - {t.lower() for t in allowlist()}
            if unknown:
                return (
                    f"SQL 引用了不在册的表 {sorted(unknown)}（幻觉表），调用被拦截",
                    {"tool": tu.name, "unknown_tables": sorted(unknown)},
                )
        return None
```

`src/sluice/tools/query_data.py` 的 `QueryDataTool` 增加方法（放在 `__init__` 之后）：

```python
    _tables: list[str] | None = None

    def table_allowlist(self) -> list[str]:
        """供输入门禁做幻觉表校验：sqlite_master 在册表名，惰性查询并缓存。"""
        if self._tables is None:
            conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "select name from sqlite_master where type='table'"
                ).fetchall()
            finally:
                conn.close()
            self._tables = [r[0] for r in rows]
        return self._tables
```

（`_tables` 是实例属性赋值，写成 `self._tables = None` 放进 `__init__` 更符合 RUF012；若 ruff 报 ClassVar 相关告警按提示调整。）

- [ ] **Step 4: 跑测试通过 + 全量回归** — `.venv/Scripts/python -m pytest tests/ -q` 全绿（71+新增），`.venv/Scripts/python -m ruff check src/ tests/` 零告警
- [ ] **Step 5: Commit** — `git add src/sluice/guardrails/input_gate.py src/sluice/tools/query_data.py tests/test_input_gate.py && git commit -m "feat: 输入门禁(幻觉工具/参数schema/SQL幻觉表拦截)"`

---

### Task 3: 行为门禁（重复调用打断 + token 预算）

**Files:**
- Create: `src/sluice/guardrails/behavior_gate.py`
- Modify: `src/sluice/exceptions.py`（+ `BudgetExceededError`）
- Test: `tests/test_behavior_gate.py`

- [ ] **Step 1: 写失败测试**

```python
"""tests/test_behavior_gate.py"""
import pytest

from sluice.exceptions import BudgetExceededError  # noqa: F401 — 断言异常类存在可抛
from sluice.guardrails.behavior_gate import BehaviorGate, call_key
from sluice.llm.types import ToolUseBlock


def _tu(name="q", sql="select 1"):
    return ToolUseBlock("t1", name, {"sql": sql})


def test_call_key_order_insensitive():
    a = ToolUseBlock("t", "x", {"a": 1, "b": 2})
    b = ToolUseBlock("t", "x", {"b": 2, "a": 1})
    assert call_key(a) == call_key(b)


def test_repeated_call_in_next_round_blocked():
    g = BehaviorGate(token_budget=100_000)
    assert g.check_call(_tu()) is None
    g.commit_round([_tu()])
    reason = g.check_call(_tu())
    assert reason is not None and "重复调用" in reason


def test_different_args_not_blocked():
    g = BehaviorGate(token_budget=100_000)
    g.check_call(_tu())
    g.commit_round([_tu()])
    assert g.check_call(_tu(sql="select 2")) is None


def test_repeat_allowed_after_gap_round():
    """A → B → A 不算连续重复：中间隔了一轮不同的调用。"""
    g = BehaviorGate(token_budget=100_000)
    g.check_call(_tu())
    g.commit_round([_tu()])
    g.check_call(_tu(sql="select 2"))
    g.commit_round([_tu(sql="select 2")])
    assert g.check_call(_tu()) is None


def test_token_budget_exceeded():
    g = BehaviorGate(token_budget=100)
    assert g.record_usage({"input_tokens": 80, "output_tokens": 10}) is None
    assert "token 预算超限" in g.record_usage({"input_tokens": 80, "output_tokens": 10})


def test_token_budget_zero_disables_check():
    g = BehaviorGate(token_budget=0)
    assert g.record_usage({"input_tokens": 10**9}) is None


def test_budget_exceeded_error_is_sluice_error():
    with pytest.raises(BudgetExceededError):
        raise BudgetExceededError("x")
```

- [ ] **Step 2: 跑测试确认失败** — FAIL `ModuleNotFoundError`
- [ ] **Step 3: 实现** — `src/sluice/exceptions.py` 追加：

```python
class BudgetExceededError(SluiceError):
    """ReAct 循环超出 token 预算（终止级门禁）。"""
```

`src/sluice/guardrails/behavior_gate.py`：

```python
"""行为门禁（拦截点②，循环运行中）：连续重复调用打断 + token 预算。"""

from __future__ import annotations

import json

from ..llm.types import ToolUseBlock


def call_key(tu: ToolUseBlock) -> str:
    """调用指纹：工具名 + 规范化参数（键序无关）。"""
    return f"{tu.name}:{json.dumps(tu.input, sort_keys=True, ensure_ascii=False)}"


class BehaviorGate:
    """run 级状态（每次 run 新建实例）——跨 run 不共享记忆。

    重复判定：与**上一轮**完全相同的调用（工具名+参数一致）→ 打断。
    A→B→A 隔轮重复不算，留给 P3 evals 观察是否需要收紧。
    """

    def __init__(self, token_budget: int):
        self._budget = token_budget
        self._tokens_used = 0
        self._prev_keys: set[str] = set()

    def check_call(self, tu: ToolUseBlock) -> str | None:
        """工具执行前调用。返回 None 放行，返回打断原因（回喂模型）。"""
        if call_key(tu) in self._prev_keys:
            return (
                f"检测到与上一轮完全相同的重复调用 {tu.name}，已强制打断："
                "请换一种方法推进，或直接给出最终回答"
            )
        return None

    def commit_round(self, tool_uses: list[ToolUseBlock]) -> None:
        """每轮工具执行完后登记本轮调用指纹，作为下一轮的比对基准。"""
        self._prev_keys = {call_key(tu) for tu in tool_uses}

    def record_usage(self, usage: dict) -> str | None:
        """每轮末累计 token；超预算返回终止原因（loop 据此抛 BudgetExceededError）。"""
        self._tokens_used += int(usage.get("input_tokens") or 0) + int(
            usage.get("output_tokens") or 0
        )
        if self._budget and self._tokens_used > self._budget:
            return f"token 预算超限：已用 {self._tokens_used} > 预算 {self._budget}"
        return None
```

- [ ] **Step 4: 跑测试通过 + 全量回归 + ruff**
- [ ] **Step 5: Commit** — `git add src/sluice/guardrails/behavior_gate.py src/sluice/exceptions.py tests/test_behavior_gate.py && git commit -m "feat: 行为门禁(连续重复调用打断+token预算)"`

---

### Task 4: 输出门禁（契约校验）

**Files:**
- Create: `src/sluice/guardrails/output_gate.py`
- Test: `tests/test_output_gate.py`

- [ ] **Step 1: 写失败测试**

```python
"""tests/test_output_gate.py"""
from sluice.guardrails.output_gate import check_output


def test_empty_answer_rejected():
    assert check_output("", []) is not None
    assert check_output("   \n  ", []) is not None


def test_missing_section_rejected():
    reason = check_output("# 周报\n正文", ["核心指标", "重点发现"])
    assert reason is not None and "重点发现" in reason and "核心指标" not in reason


def test_missing_sections_plural():
    reason = check_output("随便", ["核心指标", "重点发现"])
    assert "核心指标" in reason and "重点发现" in reason


def test_pass_with_sections():
    assert check_output("## 核心指标\nx\n## 重点发现\ny", ["核心指标", "重点发现"]) is None


def test_pass_without_contract():
    assert check_output("任意非空回答", []) is None
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** — `src/sluice/guardrails/output_gate.py`：

```python
"""输出门禁（拦截点③，产出交付前）：非空 + 必需小节。

确定性判断：小节标题是 Skill 在 frontmatter 里声明的产出契约（output_contract），
门禁只查"在不在"，不查"写得好不好"——语义质量归 P3 evals。
"""

from __future__ import annotations


def check_output(answer: str, required_sections: list[str]) -> str | None:
    """返回 None 放行；返回拦截原因（含缺失清单，可直接回喂模型自修）。"""
    if not answer or not answer.strip():
        return "最终回答为空"
    missing = [s for s in required_sections if s not in answer]
    if missing:
        return f"最终回答缺少必需小节: {'、'.join(missing)}"
    return None
```

- [ ] **Step 4: 跑测试通过 + 全量回归 + ruff**
- [ ] **Step 5: Commit** — `git add src/sluice/guardrails/output_gate.py tests/test_output_gate.py && git commit -m "feat: 输出门禁(产出契约校验:非空+必需小节)"`

---

### Task 5: GuardrailChain + 循环接线 + 恢复策略（核心任务）

**Files:**
- Create: `src/sluice/guardrails/chain.py`
- Modify: `src/sluice/config/settings.py`
- Modify: `src/sluice/runtime/loop.py`（run() 接入三拦截点 + 恢复策略）
- Modify: `src/sluice/web/app.py`、`src/sluice/cli/main.py`（组装 chain）
- Modify: `.gitignore`（+ `audit/`）
- Test: `tests/test_runtime_guardrails.py`

- [ ] **Step 1: 写失败测试**

```python
"""tests/test_runtime_guardrails.py"""
import json

from sluice.config.settings import Settings
from sluice.guardrails.chain import GuardrailChain
from sluice.llm.types import LLMResponse, TextBlock, ToolUseBlock
from sluice.runtime.loop import ReActRuntime
from sluice.tools.base import Tool, ToolResult
from sluice.tools.registry import ToolRegistry


class ReportTool(Tool):
    """带 skill 前缀的桩工具，模拟 Skill 私有工具。"""

    name = "demo.report"
    description = "demo"
    parameters = {"type": "object", "properties": {}}

    def execute(self, args):
        return ToolResult("done")


class ScriptedClient:
    """按剧本回放 LLMResponse，序列耗尽即 fail（防止测试静默通过）。"""

    def __init__(self, responses):
        self._responses = list(responses)

    def chat(self, messages, system=None, tools=None):
        assert self._responses, "剧本耗尽：测试没预期到这次 chat 调用"
        return self._responses.pop(0)


def _final(text, usage=None):
    return LLMResponse(content=[TextBlock(text)], stop_reason="end_turn", usage=usage or {})


def _call(name, args=None):
    return LLMResponse(
        content=[ToolUseBlock("t1", name, args or {})], stop_reason="tool_use", usage={}
    )


def _runtime(responses, tmp_path, **chain_kwargs):
    registry = ToolRegistry()
    registry.register(ReportTool())
    settings = Settings(audit_dir=str(tmp_path / "audit"), **chain_kwargs)
    chain = GuardrailChain(settings)
    rt = ReActRuntime(
        ScriptedClient(responses), registry, max_rounds=6, guardrails=chain
    )
    return rt, settings


def _events_of(rt):
    events = []
    rt.run("go", on_event=events.append)
    return events


def test_hallucinated_tool_blocked_and_audited(tmp_path):
    rt, settings = _runtime(
        [_call("ghost_tool"), _final("完成")], tmp_path
    )
    events = _events_of(rt)
    g = [e for e in events if e["type"] == "guardrail"]
    assert len(g) == 1
    assert g[0]["gate"] == "input" and g[0]["action"] == "blocked"
    assert "幻觉工具" in g[0]["reason"]
    obs = [e for e in events if e["type"] == "observation"][0]
    assert obs["is_error"] is True and "门禁拦截" in obs["observation"]
    audit_lines = (tmp_path / "audit" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(audit_lines[0])["kind"] == "input_blocked"


def test_schema_violation_blocked(tmp_path):
    rt, _ = _runtime(
        [_call("demo.report", {"text": 1}), _final("完成")], tmp_path
    )
    g = [e for e in _events_of(rt) if e["type"] == "guardrail"]
    assert g and "参数校验未通过" in g[0]["reason"]


def test_repeated_call_interrupted(tmp_path):
    rt, _ = _runtime(
        [_call("demo.report"), _call("demo.report"), _final("完成")], tmp_path
    )
    g = [e for e in _events_of(rt) if e["type"] == "guardrail"]
    assert any(e["gate"] == "behavior" and e["action"] == "blocked" for e in g)


def test_token_budget_exceeded_raises_with_error_event(tmp_path):
    rt, _ = _runtime(
        [_call("demo.report", usage=None), ], tmp_path, react_token_budget=10
    )
    # 重造：_runtime 不便塞 usage，直接手搓
    registry = ToolRegistry()
    registry.register(ReportTool())
    settings = Settings(audit_dir=str(tmp_path / "a2"), react_token_budget=10)
    client = ScriptedClient(
        [
            LLMResponse(
                content=[ToolUseBlock("t1", "demo.report", {})],
                stop_reason="tool_use",
                usage={"input_tokens": 80, "output_tokens": 50},
            )
        ]
    )
    rt2 = ReActRuntime(client, registry, max_rounds=5, guardrails=GuardrailChain(settings))
    events = []
    import pytest

    with pytest.raises(Exception) as exc_info:
        rt2.run("go", on_event=events.append)
    assert "token 预算超限" in str(exc_info.value)
    assert [e["type"] for e in events][-1] == "error"


def test_output_gate_repair_success(tmp_path):
    """第一轮回答缺小节 → 拦截 + 自修一轮 → 复检通过采用修复稿。"""
    registry = ToolRegistry()
    registry.register(ReportTool())
    settings = Settings(audit_dir=str(tmp_path / "a3"))
    chain = GuardrailChain(settings)
    chain._contracts = {"demo": ["核心指标", "重点发现"]}  # noqa: SLF001 — 测试直填契约
    client = ScriptedClient(
        [_final("短回答"), _final("## 核心指标\n5\n## 重点发现\n好")]
    )
    rt = ReActRuntime(client, registry, max_rounds=5, guardrails=chain)
    events = []
    answer = rt.run("go", on_event=events.append)
    assert "重点发现" in answer
    g = [e for e in events if e["type"] == "guardrail"]
    assert [e["action"] for e in g] == ["blocked", "repaired"]
    assert events[-1]["type"] == "final" and events[-1]["answer"] == answer
    audit_lines = (tmp_path / "a3" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in audit_lines]
    assert kinds == ["output_rejected", "output_repaired"]


def test_output_gate_degraded_delivery(tmp_path):
    """自修复检仍未通过 → 降级交付原稿 + 审计副本，用户端不悬挂。"""
    registry = ToolRegistry()
    registry.register(ReportTool())
    settings = Settings(audit_dir=str(tmp_path / "a4"))
    chain = GuardrailChain(settings)
    chain._contracts = {"demo": ["核心指标"]}  # noqa: SLF001
    client = ScriptedClient([_final("原稿"), _final("还是不行")])
    rt = ReActRuntime(client, registry, max_rounds=5, guardrails=chain)
    events = []
    answer = rt.run("go", on_event=events.append)
    assert answer == "原稿"
    g = [e for e in events if e["type"] == "guardrail"]
    assert [e["action"] for e in g] == ["blocked", "degraded"]
    assert events[-1]["type"] == "final"


def test_guardrails_disabled_is_kill_switch(tmp_path):
    """guardrails_enabled=False → 行为回到 P1：未知工具旧路径，无 guardrail 事件。"""
    registry = ToolRegistry()
    registry.register(ReportTool())
    settings = Settings(audit_dir=str(tmp_path / "a5"), guardrails_enabled=False)
    client = ScriptedClient([_call("ghost_tool"), _final("完成")])
    rt = ReActRuntime(client, registry, max_rounds=5, guardrails=None)
    events = []
    assert rt.run("go", on_event=events.append) == "完成"
    assert not [e for e in events if e["type"] == "guardrail"]
    obs = [e for e in events if e["type"] == "observation"][0]
    assert "未知工具" in obs["observation"]
```

- [ ] **Step 2: 跑测试确认失败** — FAIL（GuardrailChain 不存在）
- [ ] **Step 3: 实现 chain.py**：

```python
"""GuardrailChain——四个拦截点的编排入口。

None 即关闭（kill switch）：ReActRuntime 接受 guardrails=None，行为回到 P1。
"""

from __future__ import annotations

from ..config.settings import Settings
from .base import AuditLog
from .behavior_gate import BehaviorGate
from .input_gate import InputGate
from .output_gate import check_output


class GuardrailChain:
    def __init__(self, settings: Settings, packages=()):
        self.input_gate = InputGate()
        self.token_budget = settings.react_token_budget
        self.audit = AuditLog(settings.audit_dir)
        # Skill 产出契约：{skill 名: [必需小节]}，来自 SKILL.md frontmatter output_contract
        self._contracts = {
            p.name: list(getattr(p, "output_contract", [])) for p in packages
        }

    def new_behavior(self) -> BehaviorGate:
        """行为门禁是 run 级有状态对象——每次 run 新建，跨 run 不共享。"""
        return BehaviorGate(self.token_budget)

    def contracts_for(self, tool_names: list[str]) -> list[str]:
        """本次 run 触达过的 Skill 的产出契约并集（工具名 {skill}.{tool} 前缀归因）。"""
        sections: list[str] = []
        for name in tool_names:
            skill = name.split(".", 1)[0] if "." in name else ""
            sections.extend(self._contracts.get(skill, []))
        return sorted(set(sections))

    def check_output(self, answer: str, required_sections: list[str]) -> str | None:
        return check_output(answer, required_sections)
```

`settings.py` 追加字段（放在 react_max_rounds 之后）：

```python
    # Guardrails（P2）
    guardrails_enabled: bool = True
    react_token_budget: int = 200_000
    audit_dir: str = "audit"
```

`.gitignore` 追加一行 `audit/`。

- [ ] **Step 4: 改 loop.py** —— 完整新版 `run()` 及新增/修改方法（`__init__` 增参 + 三个新方法 + 头部 import 调整）：

`__init__` 签名追加 `guardrails: "GuardrailChain | None" = None`，函数体加 `self._guardrails = guardrails`。文件头 import 区追加：

```python
from typing import TYPE_CHECKING

from ..exceptions import BudgetExceededError, RoundsExhaustedError
from ..guardrails.base import guardrail_event

if TYPE_CHECKING:
    from ..guardrails.chain import GuardrailChain
```

完整新版 `run()`：

```python
    def run(self, user_input: str, on_event: Callable[[dict], None] | None = None) -> str:
        # run 级 on_event 非 None 时覆盖构造器值（Flask 每请求独立队列）
        effective = on_event if on_event is not None else self._on_event
        # trace 局部累积、结束原子赋值：并发 run 互不串写，结束后 last-writer-wins
        local_trace: list[dict] = []
        messages: list[Message] = [Message("user", [TextBlock(user_input)])]
        behavior = self._guardrails.new_behavior() if self._guardrails else None
        used_tools: list[str] = []

        try:
            for round_no in range(1, self._max_rounds + 1):
                response = self._client.chat(
                    messages, system=self._system, tools=self._registry.to_defs()
                )
                self._emit(effective, {"type": "round_start", "round": round_no})
                tool_uses = response.tool_uses

                if not tool_uses:  # Final Answer → 拦截点③输出门禁 + 拦截点④自修
                    return self._deliver_output(
                        messages, effective, local_trace, round_no, response, used_tools
                    )

                messages.append(Message("assistant", response.content))
                observations = []
                for tu in tool_uses:
                    used_tools.append(tu.name)
                    obs = self._execute_tool_gated(tu, behavior)
                    self._emit(
                        effective,
                        {
                            "type": "observation",
                            "round": round_no,
                            "name": tu.name,
                            "observation": obs.content,
                            "is_error": obs.is_error,
                        },
                    )
                    observations.append(obs)
                if behavior:
                    behavior.commit_round(tool_uses)
                    over = behavior.record_usage(response.usage)
                    if over:
                        self._audit({"kind": "budget_exceeded", "round": round_no, "detail": over})
                        raise BudgetExceededError(over)
                messages.append(Message("user", observations))
                local_trace.append(self._record(round_no, response, observations))

            raise RoundsExhaustedError(f"ReAct 循环超过 {self._max_rounds} 轮未产出最终回答")
        except Exception as exc:
            self._emit(effective, {"type": "error", "message": str(exc)})
            raise
        finally:
            self.trace = local_trace  # 原子赋值：CLI 语义不变（run 后 rt.trace 仍可用）

    def _pre_tool_check(self, tu, behavior) -> tuple[str, str, dict] | None:
        """拦截点①输入门禁 + 拦截点②重复调用。返回 (gate, reason, detail) 或 None。"""
        if self._guardrails is None:
            return None
        tool = self._registry.get(tu.name)
        verdict = self._guardrails.input_gate.check(tu, tool)
        if verdict is not None:
            reason, detail = verdict
            return ("input", reason, detail)
        if behavior is not None:
            reason = behavior.check_call(tu)
            if reason is not None:
                return ("behavior", reason, {"tool": tu.name, "args": tu.input})
        return None

    def _execute_tool_gated(self, tu, behavior):
        """先过门禁再执行；被拦截则不执行，把拦截原因作为错误观察回喂模型自纠。"""
        block = self._pre_tool_check(tu, behavior)
        if block is not None:
            gate, reason, detail = block
            self._emit_event(gate, "blocked", reason, detail, tool=tu.name)
            return ToolResultBlock(tu.id, f"[门禁拦截] {reason}", is_error=True)
        return self._execute_tool(tu)

    def _deliver_output(self, messages, effective, local_trace, round_no, response, used_tools) -> str:
        """拦截点③输出门禁 + 拦截点④恢复策略：定向自修一轮，复检通过才采用。

        复检仍失败 → 降级交付原稿 + 审计副本——不悬挂用户端，拦截全程可见可审计。
        """
        answer = response.text
        if self._guardrails is None:
            self._emit(effective, {"type": "final", "answer": answer})
            local_trace.append(self._record(round_no, response, []))
            return answer

        sections = self._guardrails.contracts_for(used_tools)
        reason = self._guardrails.check_output(answer, sections)
        if reason is None:
            self._emit(effective, {"type": "final", "answer": answer})
            local_trace.append(self._record(round_no, response, []))
            return answer

        self._emit(effective, guardrail_event("output", "blocked", reason, round_no))
        self._audit({"kind": "output_rejected", "round": round_no, "reason": reason, "answer": answer})
        # 极简 retry prompt：契约置顶、错误点名、不复制文体（见知识库 minimal-retry-prompt 实录）
        retry_lines = [f"原因：{reason}"]
        if sections:
            retry_lines.append(f"必须包含以下小节标题：{'、'.join(sections)}")
        retry_lines.append("请直接重新输出完整最终回答，不要调用工具。")
        messages.append(Message("user", [TextBlock("\n".join(retry_lines))]))
        retry_resp = self._client.chat(messages, system=self._system, tools=self._registry.to_defs())
        self._emit(effective, {"type": "round_start", "round": round_no + 1})
        repaired = retry_resp.text

        repair_reason = self._guardrails.check_output(repaired, sections)
        if repair_reason is None:
            self._emit(
                effective,
                guardrail_event("output", "repaired", f"自修通过（原拦截：{reason}）", round_no + 1),
            )
            self._audit({"kind": "output_repaired", "round": round_no + 1, "original_reason": reason})
            self._emit(effective, {"type": "final", "answer": repaired})
            local_trace.append(self._record(round_no + 1, retry_resp, []))
            return repaired

        self._emit(
            effective,
            guardrail_event("output", "degraded", f"自修复检未通过：{repair_reason}", round_no + 1),
        )
        self._audit({"kind": "output_degraded", "round": round_no + 1, "reason": repair_reason, "answer": repaired})
        self._emit(effective, {"type": "final", "answer": answer})
        local_trace.append(self._record(round_no + 1, retry_resp, []))
        return answer

    def _emit_event(self, gate, action, reason, detail, **extra) -> None:
        """带当前 on_event 的 guardrail 事件发射 + 审计落盘（loop 内两处调用共用）。"""
```

——注意：上面 `_emit_event` 签名与调用处不匹配（`_execute_tool_gated` 里没有 effective 上下文），**实现时统一为**：`_execute_tool_gated` 增加 `effective` 参数并在拦截分支直接调用：

```python
    def _execute_tool_gated(self, tu, behavior, effective):
        block = self._pre_tool_check(tu, behavior)
        if block is not None:
            gate, reason, detail = block
            self._emit(effective, guardrail_event(gate, "blocked", reason, detail.get("round", 0) if False else detail and 0 or 0))
```

——**不要照抄上面两句草稿**，最终形态如下（以此为准，删掉 `_emit_event` 想法）：

```python
    def _execute_tool_gated(self, tu, behavior, effective, round_no):
        """先过门禁再执行；被拦截则不执行，把拦截原因作为错误观察回喂模型自纠。"""
        block = self._pre_tool_check(tu, behavior)
        if block is not None:
            gate, reason, detail = block
            self._emit(effective, guardrail_event(gate, "blocked", reason, round_no, **detail))
            self._audit({"kind": "input_blocked", "round": round_no, "tool": tu.name, **detail, "reason": reason})
            return ToolResultBlock(tu.id, f"[门禁拦截] {reason}", is_error=True)
        return self._execute_tool(tu)
```

run() 循环里的调用相应改为：

```python
                    obs = self._execute_tool_gated(tu, behavior, effective, round_no)
```

`_audit` 方法：

```python
    def _audit(self, entry: dict) -> None:
        if self._guardrails is not None:
            self._guardrails.audit.append(entry)
```

`_execute_tool`、`_record`、`_emit` 保持不变。模块 docstring 的事件类型清单追加一行：

```
- {"type": "guardrail", "gate": ..., "action": ..., "round": n, "reason": ..., "detail"?: {...}}
```

- [ ] **Step 5: 组装接线** —— `src/sluice/web/app.py`：import 区加 `from ..guardrails.chain import GuardrailChain`，`create_app` 中 packages 行之后、runtime 构建处改为：

```python
    packages = SkillLoader().load(skills_dir, registry) if skills_dir else []
    guardrails = GuardrailChain(settings, packages) if settings.guardrails_enabled else None
    runtime = runtime or ReActRuntime(
        client,
        registry,
        system=_build_system(packages),
        max_rounds=settings.react_max_rounds,
        guardrails=guardrails,
    )
```

`src/sluice/cli/main.py`：`_run` 里 registry 注册后加：

```python
    from ..config.settings import Settings  # 已有
    from ..guardrails.chain import GuardrailChain  # 文件顶部 import
```

（`GuardrailChain` 放文件顶部 import；构造改为：）

```python
    guardrails = GuardrailChain(settings) if settings.guardrails_enabled else None
    runtime = ReActRuntime(
        LLMClient(settings), registry, max_rounds=settings.react_max_rounds, guardrails=guardrails
    )
```

- [ ] **Step 6: 跑测试通过 + 全量回归 + ruff** — 注意现有 `test_runtime_events.py` 的 8 个用例必须原样全绿（guardrails 默认 None 不改变 P1 行为；若 Settings() 在测试 cwd 读到 .env 的 guardrails_enabled 也无碍——测试传的是显式 None）
- [ ] **Step 7: Commit** — `git add src/sluice/guardrails/chain.py src/sluice/config/settings.py src/sluice/runtime/loop.py src/sluice/web/app.py src/sluice/cli/main.py .gitignore tests/test_runtime_guardrails.py && git commit -m "feat: guardrails接入ReAct循环(四拦截点+定向自修+降级交付+kill switch)"`

---

### Task 6: 前端 guardrail 事件可视化

**Files:**
- Modify: `web/frontend/src/App.jsx`（EventLine 组件加分支）
- Test: 无单测；验收 = `npm run build` 成功 + Flask 冒烟

- [ ] **Step 1: App.jsx 的 EventLine 组件在 `error` 分支之前插入：**

```jsx
  if (ev.type === "guardrail") {
    const style =
      ev.action === "blocked"
        ? "border-amber-400 bg-amber-50 text-amber-800"
        : ev.action === "repaired"
          ? "border-emerald-300 bg-emerald-50 text-emerald-700"
          : "border-red-300 bg-red-50 text-red-700";
    const label =
      ev.action === "blocked" ? "拦截" : ev.action === "repaired" ? "自修通过" : "降级交付";
    return (
      <div className={`rounded border p-2 text-sm ${style}`}>
        🛡 门禁[{ev.gate}] {label}（第 {ev.round} 轮）：{ev.reason}
      </div>
    );
  }
```

- [ ] **Step 2: 构建 + 冒烟** — `cd web/frontend && npm run build`（无报错）；起 Flask（`.venv/Scripts/python -m sluice web --port 5001`，后台），`curl -s http://127.0.0.1:5001/` 返回新 index.html，杀进程（netstat 找 PID + taskkill）
- [ ] **Step 3: Commit** — `git add web/frontend/src/App.jsx && git commit -m "feat: 前端guardrail事件卡片(拦截琥珀/自修绿/降级红)"`

---

### Task 7: Skill ② 跨域信贷周报 + seed 扩展 + loader 契约

**Files:**
- Modify: `scripts/seed_demo_db.py`（+ credit_applications 表）
- Modify: `src/sluice/skills/loader.py`（+ output_contract 解析、owner_skill 打标）
- Modify: `src/sluice/tools/base.py`（+ owner_skill 属性）
- Modify: `skills/weekly-report/SKILL.md`（frontmatter + output_contract）
- Create: `skills/credit-weekly/SKILL.md`、`skills/credit-weekly/tools.py`、`skills/credit-weekly/config.yaml`、`skills/credit-weekly/evals/.gitkeep`
- Test: `tests/test_skill_loader.py` 追加、`tests/test_credit_weekly_skill.py` 新建

- [ ] **Step 1: seed 脚本** —— `scripts/seed_demo_db.py` 在 sales 写入提交之后、`conn.commit()` 之前追加（原 `conn.commit(); conn.close()` 改到末尾一次提交）：

```python
PRODUCTS = ["消费贷", "经营贷", "房贷"]

conn.execute("drop table if exists credit_applications")
conn.execute(
    """create table credit_applications (
        id integer primary key,
        app_date text not null,
        city text not null,
        product text not null,
        apply_amount real not null,
        approved integer not null
    )"""
)
random.seed(43)
credit_rows = []
for day in range(1, 22):
    date = f"2026-10-{day:02d}"
    bump = 1.5 if day >= 15 else 1.0
    for city in CITIES:
        for _ in range(random.randint(2, 6)):
            credit_rows.append(
                (
                    date,
                    city,
                    random.choice(PRODUCTS),
                    round(random.uniform(5_000, 500_000) * bump, 2),
                    1 if random.random() < 0.68 else 0,
                )
            )
conn.executemany(
    "insert into credit_applications (app_date, city, product, apply_amount, approved) values (?, ?, ?, ?, ?)",
    credit_rows,
)
```

末尾 print 改为 `print(f"seeded {DB} with sales={len(rows)} credit={len(credit_rows)} rows")`。跑 `.venv/Scripts/python scripts/seed_demo_db.py` 验证输出。

- [ ] **Step 2: loader 失败测试**（追加到 `tests/test_skill_loader.py`）：

```python
def test_output_contract_parsed_from_frontmatter(tmp_path):
    skill = tmp_path / "s1"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        '---\nname: s1\ndescription: d\noutput_contract: 核心指标;重点发现\n---\n正文',
        encoding="utf-8",
    )
    pkg = SkillLoader().load(tmp_path)[0]
    assert pkg.output_contract == ["核心指标", "重点发现"]


def test_output_contract_defaults_empty(tmp_path):
    skill = tmp_path / "s2"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: s2\ndescription: d\n---\n正文", encoding="utf-8")
    assert SkillLoader().load(tmp_path)[0].output_contract == []


def test_tools_tagged_with_owner_skill(tmp_path):
    skill = tmp_path / "s3"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: s3\ndescription: d\n---\n正文", encoding="utf-8")
    (skill / "tools.py").write_text(
        "from sluice.tools.base import Tool, ToolResult\n"
        "class T(Tool):\n"
        "    name = 't'\n"
        "    description = 'x'\n"
        "    parameters = {}\n"
        "    def execute(self, args):\n"
        "        return ToolResult('ok')\n"
        "TOOLS = [T()]\n",
        encoding="utf-8",
    )
    pkg = SkillLoader().load(tmp_path)[0]
    assert pkg.tools[0].owner_skill == "s3"
```

- [ ] **Step 3: 实现 loader** —— `SkillPackage` 加字段 `output_contract: list[str] = field(default_factory=list)`；`parse_skill_md` 返回值不变（仍是 dict[str,str]）；`load()` 里构造 SkillPackage 时传：

```python
                output_contract=[
                    s.strip()
                    for s in meta.get("output_contract", "").split(";")
                    if s.strip()
                ],
```

`load()` 的工具打标循环改为：

```python
            for t in tools:
                t.name = f"{meta['name']}.{t.name}"
                t.owner_skill = meta["name"]
                if registry is not None:
                    registry.register(t)
```

`src/sluice/tools/base.py` 的 `Tool` 类加类属性：`owner_skill: str | None = None  # Skill 私有工具由 loader 打标，内置/MCP 工具为 None`

- [ ] **Step 4: weekly-report 契约声明** —— `skills/weekly-report/SKILL.md` frontmatter 加一行（放 description 之后）：

```
output_contract: 核心指标;重点发现
```

（正文已有这两个小节约定，无需改正文。）

- [ ] **Step 5: Skill ② 文件** —— `skills/credit-weekly/SKILL.md`：

```markdown
---
name: credit-weekly
description: 信贷业务周报：统计上周（自然周）贷款申请量、审批通过率、件均申请金额及环比变化，渲染为结构化周报
output_contract: 核心指标;重点发现
---

# 信贷周报 Skill

数据表：`credit_applications`（app_date / city / product / apply_amount / approved）。

## 流程

1. 用 `credit-weekly.query_credit` 探查表结构与日期范围（不要假设字段，先 SELECT LIMIT 3 看样例）
2. 两条 SQL 分别聚合上周与上上周（自然周，周一为一周开始）：申请量 COUNT(*)、审批通过率 SUM(approved)*1.0/COUNT(*)、件均申请金额（仅统计 approved=1）
3. 数据齐了直接给出最终回答，包含「核心指标」「重点发现」两个小节，环比口径：(本周-上周)/上周，上周为 0 时注明"不可比"

## 统计口径

- 自然周：Monday–Sunday，禁止用 MAX(app_date) 当"今天"锚点
- 通过率保留 1 位小数百分比；金额保留 2 位小数
- 日期覆盖不足 7 天时在报告中注明
```

`skills/credit-weekly/tools.py`：

```python
"""credit-weekly 私有工具：只读查询信贷表，表级权限收窄（越权查 sales 会被输入门禁拦截）。"""

from sluice.config.settings import Settings
from sluice.tools.query_data import QueryDataTool


class QueryCreditTool(QueryDataTool):
    name = "query_credit"
    description = "只读查询信贷申请表 credit_applications（SQLite，参数 sql 必须为 SELECT/WITH 单条语句）。"

    def table_allowlist(self):
        return ["credit_applications"]


TOOLS = [QueryCreditTool(Settings().demo_db_path)]
```

`skills/credit-weekly/config.yaml`：

```yaml
datasource: platform-demo
```

`skills/credit-weekly/evals/.gitkeep` 空文件。

- [ ] **Step 6: Skill ② 失败测试** —— `tests/test_credit_weekly_skill.py`：

```python
"""tests/test_credit_weekly_skill.py"""
from pathlib import Path

import pytest

from sluice.config.settings import Settings
from sluice.skills.loader import SkillLoader
from sluice.tools.registry import ToolRegistry

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@pytest.fixture
def credit_tool(tmp_path, monkeypatch):
    """临时 seed 一个只有 credit_applications 的库，注册 credit-weekly 包。"""
    import sqlite3

    db = tmp_path / "credit.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "create table credit_applications (id integer primary key, app_date text,"
        " city text, product text, apply_amount real, approved integer)"
    )
    conn.execute(
        "insert into credit_applications values (1, '2026-10-13', '杭州', '消费贷', 100000.0, 1)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(Settings, "model_config", {**Settings.model_config, "env_file": str(tmp_path / "none.env")})
    registry = ToolRegistry()
    packages = SkillLoader().load(SKILLS_DIR, registry)
    pkg = next(p for p in packages if p.name == "credit-weekly")
    tool = pkg.tools[0]
    tool._db_path = str(db)  # 指向临时库
    tool._tables = None  # 清缓存，重新惰性加载
    return tool


def test_credit_skill_registered_with_prefix_and_contract(credit_tool):
    assert credit_tool.name == "credit-weekly.query_credit"
    assert credit_tool.owner_skill == "credit-weekly"


def test_credit_tool_queries_credit_table(credit_tool):
    result = credit_tool.execute({"sql": "SELECT COUNT(*) AS n FROM credit_applications"})
    assert result.is_error is False and "1" in result.content


def test_credit_tool_blocks_cross_domain_table(credit_tool):
    """表级权限：跨域查 sales 应被工具自身拒绝（sales 不在 WHERE 之外的世界里——表不存在）。"""
    result = credit_tool.execute({"sql": "SELECT * FROM sales"})
    assert result.is_error is True


def test_credit_tool_allowlist_declared(credit_tool):
    assert credit_tool.table_allowlist() == ["credit_applications"]


def test_weekly_report_contract_declared():
    registry = ToolRegistry()
    packages = SkillLoader().load(SKILLS_DIR, registry)
    weekly = next(p for p in packages if p.name == "weekly-report")
    assert weekly.output_contract == ["核心指标", "重点发现"]
```

（注意：`test_credit_tool_blocks_cross_domain_table` 依赖工具侧 `mode=ro` 连接下 sales 表不存在于临时库 → SQL 报错 is_error=True，这是工具自身行为；门禁层的表级拦截已由 `test_input_gate.py` 覆盖，这里测的是"临时库没 sales 表"的兜底。若想让门禁层也参与，属 Task 8 演示脚本职责。）
- [ ] **Step 7: 全量回归 + ruff + seed 重跑**
- [ ] **Step 8: Commit** — `git add scripts/seed_demo_db.py src/sluice/skills/loader.py src/sluice/tools/base.py skills/ tests/test_skill_loader.py tests/test_credit_weekly_skill.py && git commit -m "feat: Skill②信贷周报(跨域)+loader产出契约+owner_skill打标+seed信贷表"`

---

### Task 8: 确定性演示脚本 + 端到端验收（出口标志）

**Files:**
- Create: `scripts/demo_guardrails.py`
- 无新增测试文件；验收 = 演示脚本输出 + 真网关 E2E + 全量回归

- [ ] **Step 1: 演示脚本**（确定性，不依赖网关——"现场演示幻觉拦截+自修复"的可靠载体）：

```python
"""Guardrails 现场演示（确定性，不依赖网关）：幻觉工具拦截 + 重复调用打断 + 产出自修复。

用法: .venv/Scripts/python scripts/demo_guardrails.py
"""

from sluice.config.settings import Settings
from sluice.guardrails.chain import GuardrailChain
from sluice.llm.types import LLMResponse, TextBlock, ToolUseBlock
from sluice.runtime.loop import ReActRuntime
from sluice.skills.loader import SkillPackage
from sluice.tools.base import Tool, ToolResult
from sluice.tools.registry import ToolRegistry


class DemoReportTool(Tool):
    name = "demo.report"
    description = "演示用产出工具"
    parameters = {"type": "object", "properties": {}}

    def execute(self, args):
        return ToolResult("done")


def resp_text(text):
    return LLMResponse(content=[TextBlock(text)], stop_reason="end_turn", usage={})


def resp_call(name, args=None):
    return LLMResponse(
        content=[ToolUseBlock("t1", name, args or {})], stop_reason="tool_use", usage={}
    )


class ScriptedClient:
    def __init__(self, responses):
        self._responses = list(responses)

    def chat(self, messages, system=None, tools=None):
        return self._responses.pop(0)


def main():
    registry = ToolRegistry()
    registry.register(DemoReportTool())
    packages = [
        SkillPackage(name="demo", description="演示 Skill", path=None, output_contract=["核心指标", "重点发现"])
    ]
    chain = GuardrailChain(Settings(audit_dir="audit"), packages)
    client = ScriptedClient(
        [
            resp_call("ghost_tool"),  # 幻觉工具 → 输入门禁拦截
            resp_call("demo.report"),  # 合法调用
            resp_call("demo.report"),  # 重复调用 → 行为门禁打断
            resp_text("太短了"),  # 缺小节 → 输出门禁拦截 → 自修
            resp_text("## 核心指标\n总申请量 1,234 件\n## 重点发现\n通过率环比上升"),
        ]
    )
    rt = ReActRuntime(client, registry, max_rounds=8, guardrails=chain)
    print("--- 事件流 ---")
    rt.run("出一份 demo 报告", on_event=lambda e: print(e))
    print("--- 审计落盘 ---")
    print((Settings(audit_dir="audit").audit_dir + "/audit.jsonl"))


if __name__ == "__main__":
    main()
```

（若 `SkillPackage` 构造签名不接受 `output_contract` kwarg，说明 Task 7 的 dataclass 字段没落地——回头修 Task 7，不要绕。）跑通后输出应含：`gate=input action=blocked`（幻觉工具）、`gate=behavior action=blocked`（重复调用）、`gate=output action=blocked` → `action=repaired`、最终 final 含两个小节。

- [ ] **Step 2: 真网关 E2E** —— seed 后起服务（`.venv/Scripts/python -m sluice web --port 5001`，带 `-RedirectStandardOutput/Error` 日志）：
  - **正向回归**：`POST /api/chat` "生成上周的数据周报" → 流式读完（curl `-N`）→ final 含「核心指标」「重点发现」，且事件流里可能出现 `gate=output action=blocked → repaired`（模型若一次达标则无，属正常）；周报质量不劣化
  - **Skill ② 正向**：`POST /api/chat` "生成上周的信贷周报" → final 含「核心指标」「重点发现」且数据来自 credit_applications
  - 中文 body 用 UTF-8 文件 + `--data-binary`（git-bash 内联中文会损毁）；解析流用 venv python `-X utf8`
  - **负向**：`POST /api/chat` "你好" → 无工具调用直接回答，guardrails 不误伤
- [ ] **Step 3: 全量回归** — `.venv/Scripts/python -m pytest tests/ -q` 全绿；`.venv/Scripts/python -m ruff check src/ tests/ scripts/` 零告警
- [ ] **Step 4: Commit（若有修复）** → superpowers:finishing-a-development-branch 收尾：合并回 main（P0/P1 先例：本地合并），删分支
- [ ] **Step 5: 沉淀** — Obsidian `03-项目经验/Sluice/02-阶段实录/` 写 P2 实录；TODO.md 勾 P2；更新 memory

---

## Self-Review 结论（计划作者已跑）

- **Spec 覆盖**：设计文档 §4 四拦截点 → T2（①输入）/T3（②行为）/T4（③输出）/T5（④恢复：自修一轮+复检采用+审计副本）；"留痕即产品能力" → T1 审计 + T5 事件 + T6 可视化；Skill ② → T7；出口标志 → T8 演示脚本 + E2E。§4 "权限校验（Skill 只能调声明过的工具）" 部分落地为 owner_skill 打标 + 表级权限收窄（QueryCreditTool），run 级意图权限留给 P4（已在"明确不做"声明）。
- **占位符扫描**：Task 5 Step 4 保留了两段"草稿误写"对照（`_emit_event` 弃案 + 最终形态），这是给实现者的显式警示，非占位符；其余每步含完整代码。
- **类型一致性**：`guardrail_event(gate, action, reason, round_no, **detail)` 与 loop.py 调用处一致；`BehaviorGate.check_call/commit_round/record_usage` 与 chain/loop 一致；`SkillPackage.output_contract` 在 T7 定义、T5 chain 用 `getattr(p, "output_contract", [])` 兜底（T5 先于 T7 落地时不炸）；`table_allowlist` 在 T2 duck-type 探测、T7 QueryCreditTool 覆写，契约一致。
- **风险提示**：T5 改 run() 是 P1 事件契约的紧邻改动，实现者必须保持 5 事件序列对 guardrails=None 的用例逐字节不变（test_runtime_events.py 是红线）。
