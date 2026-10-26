import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useEffect, useRef, useState } from "react";

// 空状态示例指令（点击填入输入框——渲染层最小交互）
const EXAMPLE_COMMANDS = ["生成上周的用户活跃周报", "查看当前可用的数据表和字段"];

/* 时间线行：左侧竖轨上的节点圆点 + 内容 */
function TimelineRow({ className, dot, children }) {
  return (
    <div className={`anim-entry relative pl-6 ${className}`}>
      <span
        aria-hidden="true"
        className={`absolute top-[6px] left-[1px] h-2.5 w-2.5 rounded-full shadow-[0_0_0_3px_var(--paper)] ${dot}`}
      />
      {children}
    </div>
  );
}

function EventLine({ ev }) {
  if (ev.type === "round_start") {
    return (
      <TimelineRow className="mt-4 first:mt-0" dot="bg-brand">
        <div className="flex items-center gap-2.5">
          <span className="font-mono text-[11px] font-semibold tracking-[0.18em] text-ink2 uppercase">
            ROUND {String(ev.round).padStart(2, "0")}
          </span>
          <span aria-hidden="true" className="h-px flex-1 bg-rail" />
        </div>
      </TimelineRow>
    );
  }
  if (ev.type === "tool_call") {
    return (
      <TimelineRow className="mt-2" dot="bg-brand">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 py-0.5">
          <span className="inline-flex items-center gap-1 rounded-md border border-brand/40 bg-brandsoft px-1.5 py-0.5 font-mono text-xs font-medium text-brand">
            <span aria-hidden="true">⚙</span>
            {ev.name}
          </span>
          <code className="min-w-0 max-w-full overflow-x-auto rounded-md border border-line bg-code px-1.5 py-0.5 font-mono text-xs text-ink2">
            {JSON.stringify(ev.args)}
          </code>
        </div>
      </TimelineRow>
    );
  }
  if (ev.type === "observation") {
    return (
      <TimelineRow className="mt-1" dot="bg-brand/50">
        <details className="group py-0.5 text-sm">
          <summary
            className={`inline-flex cursor-pointer list-none items-center gap-1 font-mono text-xs transition-colors ${
              ev.is_error ? "text-danger hover:opacity-75" : "text-ink2 hover:text-ink"
            }`}
          >
            <span
              aria-hidden="true"
              className="inline-block w-3 text-center text-xs transition-transform group-open:rotate-90"
            >
              ▸
            </span>
            观察：{ev.name}
            {ev.is_error ? "（出错）" : ""}
          </summary>
          <pre className="mt-1 max-h-40 overflow-auto rounded-md border border-line bg-code p-2 font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink">
            {ev.observation}
          </pre>
        </details>
      </TimelineRow>
    );
  }
  if (ev.type === "guardrail") {
    const stamp =
      ev.action === "blocked"
        ? { cls: "stamp-blocked", label: "BLOCKED", dot: "bg-accent" }
        : ev.action === "repaired"
          ? { cls: "stamp-repaired", label: "REPAIRED", dot: "bg-ok" }
          : { cls: "stamp-degraded", label: "DEGRADED", dot: "bg-danger" };
    return (
      <TimelineRow className="mt-2" dot={stamp.dot}>
        <div className={`stamp ${stamp.cls} px-3.5 py-2.5`}>
          <div className="flex items-baseline justify-between gap-3">
            <span
              className="font-mono text-[11px] font-semibold tracking-[0.22em]"
              style={{ color: "var(--stampc)" }}
            >
              {stamp.label}
            </span>
            <span className="font-mono text-[10px] tracking-[0.12em] text-ink2">
              GATE {ev.gate} · ROUND {String(ev.round).padStart(2, "0")}
            </span>
          </div>
          <p className="mt-1.5 text-sm leading-relaxed">{ev.reason}</p>
        </div>
      </TimelineRow>
    );
  }
  if (ev.type === "error") {
    return (
      <TimelineRow className="mt-2" dot="bg-danger">
        <div className="stamp stamp-degraded px-3.5 py-2.5">
          <div className="flex items-baseline justify-between gap-3">
            <span className="font-mono text-[11px] font-semibold tracking-[0.22em] text-danger">
              ERROR
            </span>
          </div>
          <p className="mt-1.5 text-sm leading-relaxed">运行出错：{ev.message}</p>
        </div>
      </TimelineRow>
    );
  }
  return null;
}

export default function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // 纯 updater：最后一条 run 消息追加本地合成 error 事件（无则新建 run 消息）
  function appendLocalError(message) {
    setMessages((m) => {
      const last = m[m.length - 1];
      if (last && last.role === "run") {
        return m.map((msg, i) =>
          i === m.length - 1
            ? { ...msg, events: [...msg.events, { type: "error", message }] }
            : msg
        );
      }
      return [...m, { role: "run", events: [{ type: "error", message }] }];
    });
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setBusy(true);
    setMessages((m) => [...m, { role: "user", text }]);

    let es;
    try {
      const runId = (
        await (
          await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text }),
          })
        ).json()
      ).run_id;

      setMessages((m) => [...m, { role: "run", events: [] }]);
      es = new EventSource(`/api/stream/${runId}`);
    } catch (err) {
      appendLocalError("发送失败:" + err.message);
      setBusy(false);
      return;
    }

    es.onmessage = (e) => {
      let ev;
      try {
        ev = JSON.parse(e.data);
      } catch {
        es.close();
        appendLocalError("收到非 JSON 事件");
        setBusy(false);
        return;
      }
      if (ev.type === "final") {
        es.close();
        setMessages((m) =>
          m.map((msg, i) =>
            i === m.length - 1 && msg.role === "run" ? { ...msg, answer: ev.answer } : msg
          )
        );
        setBusy(false);
      } else {
        setMessages((m) =>
          m.map((msg, i) =>
            i === m.length - 1 && msg.role === "run" ? { ...msg, events: [...msg.events, ev] } : msg
          )
        );
      }
    };
    es.onerror = () => {
      es.close();
      appendLocalError("连接中断");
      setBusy(false);
    };
  }

  return (
    <div className="flex h-screen flex-col">
      {/* 头部 = 仪表铭牌 */}
      <header className="anim-rise shrink-0 border-b border-line">
        <div className="mx-auto flex h-14 w-full max-w-3xl items-center gap-3 px-4 sm:px-6">
          <span className="font-mono text-base font-semibold tracking-[0.2em] text-ink uppercase">
            SLUICE
          </span>
          <span
            aria-hidden="true"
            className={`h-2 w-2 rounded-full bg-accent ${busy ? "breathe" : ""}`}
          />
          <span className="font-mono text-xs text-ink2">{busy ? "运行中" : "空闲"}</span>
          <span className="ml-auto hidden font-mono text-[11px] tracking-[0.08em] text-ink2 sm:block">
            SKILL-MOUNTED DATA AGENT
          </span>
        </div>
      </header>

      {/* 消息流 = 航行记录时间线 */}
      <main className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 py-6 sm:px-6">
          {messages.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center py-16 text-center">
              <div className="w-full max-w-md rounded-2xl border border-line bg-surface p-8 shadow-panel">
                <div className="font-mono text-2xl font-semibold tracking-[0.24em] text-ink">
                  SLUICE
                </div>
                <p className="mt-2 text-sm text-ink2">输入数据需求，观察 Agent 的每一步</p>
                <div className="mt-5 flex flex-col gap-2">
                  {EXAMPLE_COMMANDS.map((t) => (
                    <button
                      key={t}
                      type="button"
                      onClick={() => setInput(t)}
                      className="cursor-pointer rounded-lg border border-line bg-code px-3 py-2 text-left font-mono text-xs text-ink2 transition-colors hover:border-brand/50 hover:text-brand"
                    >
                      {t}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="space-y-5">
              {messages.map((m, i) =>
                m.role === "user" ? (
                  <div key={i} className="anim-entry flex justify-end">
                    <div className="max-w-[80%] rounded-[12px] rounded-tr-none bg-brand px-4 py-2.5 text-sm leading-relaxed text-brandfg shadow-panel">
                      {m.text}
                    </div>
                  </div>
                ) : m.role === "run" ? (
                  <div key={i} className="relative">
                    <span
                      aria-hidden="true"
                      className="absolute top-2 bottom-2 left-[5px] w-[2px] rounded-full bg-rail"
                    />
                    {m.events.map((ev, j) => (
                      <EventLine key={j} ev={ev} />
                    ))}
                    {m.answer && (
                      <TimelineRow className="mt-4" dot="bg-accent">
                        <div className="overflow-hidden rounded-xl border border-line bg-surface shadow-panel">
                          <div className="border-b border-line px-4 py-2">
                            <span className="font-mono text-[10px] font-semibold tracking-[0.22em] text-accent uppercase">
                              REPORT
                            </span>
                          </div>
                          <div className="report-md px-4 py-3">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                              {m.answer}
                            </ReactMarkdown>
                          </div>
                        </div>
                      </TimelineRow>
                    )}
                  </div>
                ) : null
              )}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </main>

      {/* 输入区 = 命令栏（贴底悬浮，上方渐隐过渡） */}
      <div className="anim-rise-2 relative shrink-0">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -top-10 right-0 left-0 h-10 bg-linear-to-t from-paper to-transparent"
        />
        <div className="mx-auto w-full max-w-3xl px-4 pb-5 sm:px-6">
          <div className="cmdbar flex items-center gap-2 rounded-2xl border border-bar bg-surface p-2 pl-4 shadow-panel">
            <input
              className="min-w-0 flex-1 border-0 bg-transparent font-mono text-sm text-ink outline-none placeholder:text-ink2/70 disabled:cursor-not-allowed disabled:opacity-50"
              value={input}
              placeholder={busy ? "Agent 运行中…" : "向 Agent 下达数据指令…"}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && send()}
              disabled={busy}
            />
            <button
              className="shrink-0 cursor-pointer rounded-xl bg-brand px-4 py-2 font-mono text-sm font-medium text-brandfg transition-colors hover:bg-brandhover focus-visible:ring-2 focus-visible:ring-brand/40 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-40"
              onClick={send}
              disabled={busy}
            >
              发送
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
