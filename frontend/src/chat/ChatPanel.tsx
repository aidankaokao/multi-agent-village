import { useEffect, useRef, useState } from "react";
import type { AgentInfo } from "../api";
import type { RunsState, RunView, StepView } from "./runs";

const SOURCE_LABEL: Record<string, string> = {
  llm: "村長規劃",
  mention: "@指名",
  keyword: "關鍵字備援",
  direct: "村長直接回覆",
};

const STATUS_ICON: Record<StepView["status"], string> = {
  pending: "⏳",
  running: "✍️",
  done: "✅",
  error: "❌",
};

const EXAMPLES = [
  "寫一首關於颱風天的詩，再把它改編成冷笑話",
  "講一個工程師的笑話",
  "@poet 寫一首五言絕句描寫月亮 → @jester",
  "你好，村裡有誰？",
];

interface Props {
  runs: RunsState;
  agents: AgentInfo[];
  onSend: (message: string) => Promise<void>;
}

export function ChatPanel({ runs, agents, onSend }: Props) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const names: Record<string, string> = { mayor: "村長", ...Object.fromEntries(agents.map((a) => [a.id, a.name])) };
  const busy = runs.order.some((id) => ["planning", "running"].includes(runs.byId[id].status));

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [runs]);

  const send = async (message: string) => {
    const m = message.trim();
    if (!m || sending) return;
    setSending(true);
    setError("");
    try {
      await onSend(m);
      setText("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="chat">
      <div className="chat-log">
        {runs.order.length === 0 && (
          <div className="chat-empty">
            <p>
              村裡的村民：
              {agents.map((a) => (
                <span key={a.id} className="tag" title={a.description}>
                  {a.name} @{a.id}
                </span>
              ))}
            </p>
            <p className="hint">試試看：</p>
            {EXAMPLES.map((ex) => (
              <button key={ex} className="example" onClick={() => setText(ex)}>
                {ex}
              </button>
            ))}
          </div>
        )}
        {runs.order.map((id) => (
          <RunBlock key={id} run={runs.byId[id]} names={names} />
        ))}
        <div ref={bottom} />
      </div>

      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        {error && <p className="error">{error}</p>}
        <textarea
          rows={3}
          value={text}
          placeholder="輸入問題，Enter 送出、Shift+Enter 換行；可用 @poet、@jester 直接指名"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send(text);
            }
          }}
        />
        <button type="submit" disabled={sending || !text.trim()}>
          {sending ? "送出中…" : busy ? "送出（村民忙碌中）" : "送出"}
        </button>
      </form>
    </div>
  );
}

function RunBlock({ run, names }: { run: RunView; names: Record<string, string> }) {
  const [open, setOpen] = useState(false);
  const finished = run.status === "done" || run.status === "failed";

  return (
    <div className="run">
      <div className="bubble user">{run.message}</div>

      <div className="bubble village">
        <div className="row">
          <strong>村長</strong>
          {run.source && <span className="tag">{SOURCE_LABEL[run.source] ?? run.source}</span>}
          {run.status === "planning" && <span className="muted">分派工作中…</span>}
        </div>

        {run.steps.length > 0 && (
          <ol className="steps">
            {run.steps.map((s) => (
              <li key={s.id} className={`step ${s.status}`}>
                <span>{STATUS_ICON[s.status]}</span> <strong>{names[s.agent] ?? s.agent}</strong>
                <span className="muted">：{s.instruction}</span>
                {s.retries > 0 && <span className="tag">重試 {s.retries}</span>}
                {s.error && <div className="step-error">{s.error}</div>}
              </li>
            ))}
          </ol>
        )}

        {run.status === "done" && run.answer && <div className="answer">{run.answer}</div>}
        {run.status === "done" && !run.answer && <div className="step-error">沒有產生結果，請展開執行過程查看原因。</div>}
        {run.status === "failed" && <div className="step-error">執行失敗：{run.error}</div>}

        {finished && (run.steps.length > 0 || run.notes.length > 0) && (
          <button className="link" onClick={() => setOpen(!open)}>
            {open ? "▾ 收起執行過程" : "▸ 執行過程"}
          </button>
        )}
        {open && (
          <div className="trace">
            {run.notes.map((n, i) => (
              <div key={i} className="muted">
                ・{n}
              </div>
            ))}
            {run.steps.map((s) => (
              <div key={s.id} className="trace-step">
                <div className="row">
                  <code>{s.id}</code>
                  <strong>{names[s.agent] ?? s.agent}</strong>
                  {s.use.length > 0 && <span className="muted">使用 {s.use.join(", ")}</span>}
                  {s.model && <span className="tag">{s.model}</span>}
                  {s.elapsed_ms !== undefined && <span className="muted">{(s.elapsed_ms / 1000).toFixed(1)}s</span>}
                </div>
                <div className="muted">指示：{s.instruction}</div>
                {s.warnings.map((w) => (
                  <div key={w} className="tag warn">
                    {w}
                  </div>
                ))}
                {s.output && <pre className="output">{s.output}</pre>}
                {s.error && <div className="step-error">{s.error}</div>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
