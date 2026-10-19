import { useEffect, useRef, useState } from "react";

function EventLine({ ev }) {
  if (ev.type === "round_start") {
    return <div className="mt-2 text-xs font-semibold text-gray-500">第 {ev.round} 轮</div>;
  }
  if (ev.type === "tool_call") {
    return (
      <div className="text-sm">
        <span className="font-mono text-indigo-600">{ev.name}</span>
        <code className="ml-2 rounded bg-gray-100 px-1 py-0.5 font-mono text-xs">
          {JSON.stringify(ev.args)}
        </code>
      </div>
    );
  }
  if (ev.type === "observation") {
    return (
      <details className="text-sm">
        <summary className={ev.is_error ? "cursor-pointer text-red-600" : "cursor-pointer text-gray-600"}>
          观察：{ev.name}
          {ev.is_error ? "（出错）" : ""}
        </summary>
        <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-xs">
          {ev.observation}
        </pre>
      </details>
    );
  }
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
  if (ev.type === "error") {
    return (
      <div className="rounded border border-red-300 bg-red-50 p-2 text-sm text-red-700">
        运行出错：{ev.message}
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
    <div className="mx-auto flex h-screen max-w-3xl flex-col p-4">
      <h1 className="mb-4 text-xl font-bold">Sluice — 可挂载 Skill 的数据 Agent 平台</h1>
      <div className="flex-1 space-y-3 overflow-y-auto rounded-lg border p-4">
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[80%] rounded-2xl bg-blue-600 px-4 py-2 text-white">{m.text}</div>
            </div>
          ) : m.role === "run" ? (
            <div key={i} className="rounded-lg bg-gray-50 p-3">
              {m.events.map((ev, j) => (
                <EventLine key={j} ev={ev} />
              ))}
              {m.answer && (
                <pre className="mt-2 whitespace-pre-wrap rounded border bg-white p-3 font-sans text-sm">
                  {m.answer}
                </pre>
              )}
            </div>
          ) : null
        )}
        <div ref={bottomRef} />
      </div>
      <div className="mt-3 flex gap-2">
        <input
          className="flex-1 rounded-lg border px-3 py-2"
          value={input}
          placeholder={busy ? "Agent 运行中…" : "例如：生成上周的数据周报"}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && send()}
          disabled={busy}
        />
        <button
          className="rounded-lg bg-blue-600 px-4 py-2 text-white disabled:opacity-50"
          onClick={send}
          disabled={busy}
        >
          发送
        </button>
      </div>
    </div>
  );
}
