import { useRef, useState } from "react";

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

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setBusy(true);
    setMessages((m) => [...m, { role: "user", text }]);

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
    const es = new EventSource(`/api/stream/${runId}`);
    es.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      if (ev.type === "final") {
        es.close();
        setMessages((m) => {
          const copy = [...m];
          const last = copy[copy.length - 1];
          if (last && last.role === "run") last.answer = ev.answer;
          return copy;
        });
        setBusy(false);
      } else {
        setMessages((m) => {
          const copy = [...m];
          const last = copy[copy.length - 1];
          if (last && last.role === "run") last.events = [...last.events, ev];
          return copy;
        });
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
      }
    };
    es.onerror = () => {
      es.close();
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
          onKeyDown={(e) => e.key === "Enter" && send()}
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
