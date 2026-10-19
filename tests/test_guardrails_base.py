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
