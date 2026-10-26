import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useEffect, useRef, useState } from "react";

// 空状态示例指令（点击填入输入框——渲染层最小交互）
const EXAMPLE_COMMANDS = ["生成上周的用户活跃周报", "查看当前可用的数据表和字段"];

const THEME_KEY = "sluice-theme";
const ACTIVE_CONV_KEY = "sluice-active-conversation";
const SIDEBAR_KEY = "sluice-sidebar";

/* 时间线行：左侧竖轨上的节点圆点 + 内容 */
function TimelineRow({ className, dot, children }) {
  return (
    <div className={`anim-entry relative pl-7 ${className}`}>
      <span
        aria-hidden="true"
        className={`absolute top-[7px] left-[1.5px] h-2 w-2 rounded-full shadow-[0_0_0_3px_var(--surface)] ${dot}`}
      />
      {children}
    </div>
  );
}

/* 极简线性图标（零依赖） */
function Icon({ path, className = "h-4 w-4" }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      {path}
    </svg>
  );
}
const ICONS = {
  panel: <path d="M4 5h16v14H4zM9 5v14" />,
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </>
  ),
  moon: <path d="M20 13.5A8 8 0 0 1 10.5 4 8 8 0 1 0 20 13.5z" />,
  system: (
    <>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8m-4-4v4" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  trash: <path d="M4 7h16M9 7V5h6v2m-8 0 1 13h8l1-13M10 11v6m4-6v6" />,
  check: <path d="M5 12l5 5 9-9" />,
};

/* 历史会话相对时间 */
function relTime(iso) {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  return `${Math.floor(h / 24)} 天前`;
}

/* 持久化轮次 → 消息流（轨迹重放复用现有渲染组件） */
function turnsToMessages(turns) {
  const out = [];
  for (const t of turns || []) {
    out.push({ role: "user", text: t.user });
    out.push({ role: "run", events: t.events || [], answer: t.answer });
  }
  return out;
}

function EventLine({ ev }) {
  if (ev.type === "round_start") {
    return (
      <TimelineRow className="mt-6 first:mt-0" dot="bg-brand">
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
      <TimelineRow className="mt-3" dot="bg-brand">
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
      <TimelineRow className="mt-2" dot="bg-brand/50">
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
      <TimelineRow className="mt-3" dot={stamp.dot}>
        <div className={`stamp ${stamp.cls} px-4 py-3.5`}>
          <div className="flex items-baseline justify-between gap-3">
            <span
              className="font-mono text-[10px] font-semibold tracking-[0.22em]"
              style={{ color: "var(--stampc)" }}
            >
              {stamp.label}
            </span>
            <span className="font-mono text-[10px] tracking-[0.12em] text-ink2">
              GATE {ev.gate} · ROUND {String(ev.round).padStart(2, "0")}
            </span>
          </div>
          <p className="mt-2 text-sm leading-relaxed">{ev.reason}</p>
        </div>
      </TimelineRow>
    );
  }
  if (ev.type === "error") {
    return (
      <TimelineRow className="mt-3" dot="bg-danger">
        <div className="stamp stamp-degraded px-4 py-3.5">
          <div className="flex items-baseline justify-between gap-3">
            <span className="font-mono text-[10px] font-semibold tracking-[0.22em] text-danger">
              ERROR
            </span>
          </div>
          <p className="mt-2 text-sm leading-relaxed">运行出错：{ev.message}</p>
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
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem(THEME_KEY) || "system";
    } catch {
      return "system";
    }
  });
  const [convos, setConvos] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_KEY) !== "closed";
    } catch {
      return true;
    }
  });
  const [confirmDel, setConfirmDel] = useState(null);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // 主题三态应用到根元素并持久化（system 态去掉属性，回落媒体查询）
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "dark" || theme === "light") root.dataset.theme = theme;
    else delete root.dataset.theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {}
  }, [theme]);

  // 历史会话：首屏拉列表 + 恢复上次会话
  function loadConvos() {
    fetch("/api/conversations")
      .then((r) => r.json())
      .then((d) => setConvos(d.conversations || []))
      .catch(() => {});
  }
  useEffect(() => {
    loadConvos();
    const last = (() => {
      try {
        return localStorage.getItem(ACTIVE_CONV_KEY);
      } catch {
        return null;
      }
    })();
    if (last) openConversation(last);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅首挂载执行
  }, []);

  function rememberActive(id) {
    setActiveId(id);
    try {
      if (id) localStorage.setItem(ACTIVE_CONV_KEY, id);
      else localStorage.removeItem(ACTIVE_CONV_KEY);
    } catch {}
  }

  async function openConversation(id) {
    setConfirmDel(null);
    try {
      const conv = await (await fetch(`/api/conversations/${id}`)).json();
      if (!conv || conv.error) {
        rememberActive(null);
        setMessages((m) => (m.length ? [] : m));
        return;
      }
      setMessages(turnsToMessages(conv.messages));
      rememberActive(id);
    } catch {
      /* 网络失败保持现状 */
    }
  }

  async function newConversation() {
    if (busy) return;
    if (activeId && messages.length === 0) return; // 已是空会话不重复建
    try {
      const { id } = await (
        await fetch("/api/conversations", { method: "POST" })
      ).json();
      setMessages([]);
      rememberActive(id);
      loadConvos();
    } catch {}
  }

  async function removeConversation(id) {
    try {
      await fetch(`/api/conversations/${id}`, { method: "DELETE" });
    } catch {}
    setConvos((c) => c.filter((x) => x.id !== id));
    setConfirmDel(null);
    if (id === activeId) {
      setMessages([]);
      rememberActive(null);
    }
  }

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
      const data = await (
        await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: text,
            ...(activeId ? { conversation_id: activeId } : {}),
          }),
        })
      ).json();
      if (data.conversation_id && data.conversation_id !== activeId) {
        rememberActive(data.conversation_id); // 无 id 发送 → 服务端新建并告知
      }

      setMessages((m) => [...m, { role: "run", events: [] }]);
      es = new EventSource(`/api/stream/${data.run_id}`);
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
        setTimeout(loadConvos, 300); // 落盘已先于 end（后端 Event 同步），稍候刷新列表
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
    <div className="flex h-screen">
      {/* 左侧历史会话栏（DeepSeek 式，可折叠） */}
      {sidebarOpen && (
        <aside className="flex w-60 shrink-0 flex-col border-r border-[color:var(--hairline)] bg-surface">
          <div className="flex h-16 shrink-0 items-center px-3">
            <button
              type="button"
              onClick={newConversation}
              disabled={busy}
              className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 rounded-xl border border-line bg-paper px-3 py-2 text-sm text-ink transition-colors hover:border-brand/40 hover:text-brand disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Icon path={ICONS.plus} className="h-4 w-4 shrink-0" />
              <span className="truncate">新对话</span>
            </button>
          </div>
          <nav className="scroll-thin min-h-0 flex-1 overflow-y-auto px-2 pb-4">
            {convos.length === 0 ? (
              <p className="px-2 pt-4 text-center text-xs text-ink2">暂无历史对话</p>
            ) : (
              convos.map((c) => (
                <div
                  key={c.id}
                  className="group relative"
                  onMouseLeave={() => setConfirmDel((d) => (d === c.id ? null : d))}
                >
                  <button
                    type="button"
                    onClick={() => openConversation(c.id)}
                    className={`w-full cursor-pointer rounded-xl px-3 py-2.5 text-left transition-colors ${
                      c.id === activeId ? "bg-brandsoft" : "hover:bg-paper"
                    }`}
                  >
                    <span
                      className={`block truncate text-sm leading-snug ${
                        c.id === activeId ? "font-medium text-brand" : "text-ink"
                      }`}
                    >
                      {c.title}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-ink2">
                      {relTime(c.updated_at)}
                    </span>
                  </button>
                  <button
                    type="button"
                    aria-label={confirmDel === c.id ? "确认删除" : "删除会话"}
                    onClick={() =>
                      confirmDel === c.id ? removeConversation(c.id) : setConfirmDel(c.id)
                    }
                    className={`absolute top-1/2 right-1.5 -translate-y-1/2 cursor-pointer rounded-md p-1.5 transition-colors ${
                      confirmDel === c.id
                        ? "text-danger opacity-100"
                        : "text-ink2 opacity-0 hover:text-danger group-hover:opacity-100"
                    }`}
                  >
                    <Icon path={confirmDel === c.id ? ICONS.check : ICONS.trash} className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))
            )}
          </nav>
        </aside>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        {/* 头部 = 仪表铭牌（与页面同底色，发丝分隔线） */}
        <header className="anim-rise shrink-0 border-b border-[color:var(--hairline)]">
          <div className="mx-auto flex h-16 w-full max-w-3xl items-center gap-3 px-4 sm:px-6">
            <button
              type="button"
              aria-label={sidebarOpen ? "收起侧栏" : "展开侧栏"}
              onClick={() => {
                setSidebarOpen((o) => {
                  try {
                    localStorage.setItem(SIDEBAR_KEY, o ? "closed" : "open");
                  } catch {}
                  return !o;
                });
              }}
              className="cursor-pointer rounded-lg p-1.5 text-ink2 transition-colors hover:bg-paper hover:text-ink"
            >
              <Icon path={ICONS.panel} className="h-[18px] w-[18px]" />
            </button>
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
            {/* 主题三态：light → dark → system */}
            <button
              type="button"
              onClick={() =>
                setTheme((t) => (t === "light" ? "dark" : t === "dark" ? "system" : "light"))
              }
              aria-label={`主题：${theme === "light" ? "浅色" : theme === "dark" ? "深色" : "跟随系统"}（点击切换）`}
              title={`主题：${theme === "light" ? "浅色" : theme === "dark" ? "深色" : "跟随系统"}`}
              className="cursor-pointer rounded-lg p-1.5 text-ink2 transition-colors hover:bg-paper hover:text-ink"
            >
              <Icon
                path={theme === "light" ? ICONS.sun : theme === "dark" ? ICONS.moon : ICONS.system}
                className="h-[18px] w-[18px]"
              />
            </button>
          </div>
        </header>

        {/* 消息流 = 航行记录时间线 */}
        <main className="scroll-thin min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col border-x border-[color:var(--hairline)] bg-surface px-4 py-10 sm:px-6">
            {messages.length === 0 ? (
              <div className="flex flex-1 flex-col items-center justify-center py-20 text-center">
                <h2 className="text-2xl font-semibold text-ink">你好，我是 Sluice</h2>
                <p className="mt-3 text-sm text-ink2">下达数据指令，每一步都可见、可信</p>
                <div className="mt-8 flex w-full max-w-md flex-col gap-2.5">
                  {EXAMPLE_COMMANDS.map((t) => (
                    <button
                      key={t}
                      type="button"
                      onClick={() => setInput(t)}
                      className="cursor-pointer rounded-full border border-line bg-paper px-4 py-2.5 text-center font-mono text-xs text-ink2 transition-colors hover:border-brand/40 hover:text-brand"
                    >
                      {t}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-7">
                {messages.map((m, i) =>
                  m.role === "user" ? (
                    <div key={i} className="anim-entry flex justify-end">
                      <div className="max-w-[80%] rounded-[18px] border border-brand/15 bg-brandsoft px-5 py-3 text-sm leading-relaxed text-brand">
                        {m.text}
                      </div>
                    </div>
                  ) : m.role === "run" ? (
                    <div key={i} className="relative">
                      <span
                        aria-hidden="true"
                        className="absolute top-2 bottom-2 left-[5px] w-px bg-rail"
                      />
                      {m.events.map((ev, j) => (
                        <EventLine key={j} ev={ev} />
                      ))}
                      {m.answer && (
                        <TimelineRow className="mt-6" dot="bg-accent">
                          <div className="overflow-hidden rounded-xl border border-line bg-surface shadow-panel">
                            <div className="border-b border-[color:var(--hairline)] px-5 py-2.5">
                              <span className="font-mono text-[10px] font-semibold tracking-[0.22em] text-accent uppercase">
                                REPORT
                              </span>
                            </div>
                            <div className="report-md px-5 py-4">
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

        {/* 输入区 = 命令栏（贴底悬浮） */}
        <div className="anim-rise-2 relative shrink-0">
          <div className="mx-auto w-full max-w-3xl border-x border-[color:var(--hairline)] bg-surface px-4 pt-2 pb-6 sm:px-6">
            <div className="cmdbar flex items-center gap-2 rounded-2xl border border-bar bg-surface p-2 pl-4">
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
    </div>
  );
}
