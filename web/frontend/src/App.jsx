import { useEffect, useRef, useState } from "react";

function EventLine({ ev }) {
  if (ev.type === "round_start") {
    return (
      <div className="mt-3 mb-1 flex items-center gap-2 first:mt-0">
        <span className="text-xs font-semibold tracking-wide text-slate-400 dark:text-slate-500">
          第 {ev.round} 轮
        </span>
        <span className="h-px flex-1 bg-slate-200 dark:bg-slate-800" />
      </div>
    );
  }
  if (ev.type === "tool_call") {
    return (
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 py-0.5 text-sm">
        <span className="inline-flex items-center gap-1 rounded bg-indigo-50 px-1.5 py-0.5 font-mono text-xs font-medium text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">
          <span aria-hidden="true">⚙</span>
          {ev.name}
        </span>
        <code className="min-w-0 max-w-full overflow-x-auto rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
          {JSON.stringify(ev.args)}
        </code>
      </div>
    );
  }
  if (ev.type === "observation") {
    return (
      <details className="group py-0.5 text-sm">
        <summary
          className={
            ev.is_error
              ? "inline-flex cursor-pointer list-none items-center gap-1 text-red-600 transition-colors hover:text-red-700 dark:text-red-400 dark:hover:text-red-300"
              : "inline-flex cursor-pointer list-none items-center gap-1 text-slate-500 transition-colors hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
          }
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
        <pre className="mt-1 max-h-40 overflow-auto rounded-md border border-slate-200 bg-slate-50 p-2 font-mono text-xs leading-relaxed whitespace-pre-wrap text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
          {ev.observation}
        </pre>
      </details>
    );
  }
  if (ev.type === "guardrail") {
    const style =
      ev.action === "blocked"
        ? "border-l-amber-400 bg-amber-50 text-amber-800 dark:border-l-amber-500 dark:bg-amber-950/40 dark:text-amber-300"
        : ev.action === "repaired"
          ? "border-l-emerald-400 bg-emerald-50 text-emerald-700 dark:border-l-emerald-500 dark:bg-emerald-950/40 dark:text-emerald-300"
          : "border-l-red-400 bg-red-50 text-red-700 dark:border-l-red-500 dark:bg-red-950/40 dark:text-red-300";
    const label =
      ev.action === "blocked" ? "拦截" : ev.action === "repaired" ? "自修通过" : "降级交付";
    return (
      <div
        className={`flex items-start gap-2 rounded-md border border-slate-200 border-l-4 p-2 text-sm leading-relaxed dark:border-slate-800 ${style}`}
      >
        <span aria-hidden="true" className="mt-0.5 shrink-0">
          🛡
        </span>
        <span>
          门禁[{ev.gate}] {label}（第 {ev.round} 轮）：{ev.reason}
        </span>
      </div>
    );
  }
  if (ev.type === "error") {
    return (
      <div className="flex items-start gap-2 rounded-md border border-slate-200 border-l-4 border-l-red-400 bg-red-50 p-2 text-sm text-red-700 dark:border-slate-800 dark:border-l-red-500 dark:bg-red-950/40 dark:text-red-300">
        <span aria-hidden="true" className="mt-0.5 shrink-0">
          ✕
        </span>
        <span>运行出错：{ev.message}</span>
      </div>
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
    <div className="mx-auto flex h-screen max-w-3xl flex-col p-4 sm:p-6">
      <header className="mb-4 flex items-center gap-2.5">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-indigo-600 text-sm font-bold text-white shadow-sm">
          S
        </div>
        <div>
          <h1 className="text-lg leading-tight font-semibold tracking-tight text-slate-900 dark:text-slate-100">
            Sluice
          </h1>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            可挂载 Skill 的数据 Agent 平台
          </p>
        </div>
      </header>
      <div className="flex-1 space-y-4 overflow-y-auto rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900 sm:p-5">
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[80%] rounded-2xl rounded-br-md bg-indigo-600 px-4 py-2 text-sm leading-relaxed text-white shadow-sm">
                {m.text}
              </div>
            </div>
          ) : m.role === "run" ? (
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-950/60">
              <div className="space-y-1">
                {m.events.map((ev, j) => (
                  <EventLine key={j} ev={ev} />
                ))}
              </div>
              {m.answer && (
                <pre className="mt-3 rounded-md border border-slate-200 bg-white p-3 text-sm leading-relaxed whitespace-pre-wrap text-slate-800 shadow-sm dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200">
                  {m.answer}
                </pre>
              )}
            </div>
          ) : null
        )}
        <div ref={bottomRef} />
      </div>
      <div className="mt-4 flex gap-2">
        <input
          className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm text-slate-900 shadow-sm transition-colors outline-none placeholder:text-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500 dark:focus:border-indigo-400 dark:disabled:bg-slate-800/50"
          value={input}
          placeholder={busy ? "Agent 运行中…" : "例如：生成上周的数据周报"}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && send()}
          disabled={busy}
        />
        <button
          className="shrink-0 cursor-pointer rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500/40 focus-visible:outline-none active:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
          onClick={send}
          disabled={busy}
        >
          发送
        </button>
      </div>
    </div>
  );
}
