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
  onReset: () => void;
}

export function ChatPanel({ runs, agents, onSend, onReset }: Props) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const log = useRef<HTMLDivElement>(null);
  const stick = useRef(true); // 使用者往上捲去看舊內容時，串流不要把畫面拉回底部
  const names: Record<string, string> = { mayor: "村長", ...Object.fromEntries(agents.map((a) => [a.id, a.name])) };
  const busy = runs.order.some((id) => ["planning", "running"].includes(runs.byId[id].status));

  useEffect(() => {
    const el = log.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [runs]);

  const send = async (message: string) => {
    const m = message.trim();
    if (!m || sending) return;
    setSending(true);
    setError("");
    stick.current = true;
    try {
      await onSend(m);
      setText("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  const reset = () => {
    onReset();
    setError("");
    stick.current = true;
  };

  return (
    <div className="chat">
      <div className="chat-bar">
        <span className="muted">{runs.order.length ? `${runs.order.length} 則問答` : "新對話"}</span>
        <button
          className="link"
          onClick={reset}
          disabled={runs.order.length === 0}
          title="清空畫面重新開始（目前每次提問彼此獨立，村長還不會記得前面的對話）"
        >
          ＋ 新對話
        </button>
      </div>

      <div
        className="chat-log"
        ref={log}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
        }}
      >
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

/** 執行中每 200ms 更新一次，用來顯示經過秒數 */
function useNow(active: boolean): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), 200);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}

const secs = (ms: number) => `${(Math.max(0, ms) / 1000).toFixed(1)}s`;

function statusText(run: RunView, names: Record<string, string>): string {
  if (run.status === "planning") return "正在想要請誰幫忙…";
  if (run.status === "failed") return "失敗";
  if (run.status === "done") return run.ok === false ? "部分失敗" : "完成";
  if (run.source === "direct") return "回覆中…";
  const active = run.steps.find((s) => s.status === "running");
  if (active) return `${names[active.agent] ?? active.agent} 工作中…`;
  return run.steps.some((s) => s.status === "pending") ? "交接中…" : "整理結果中…";
}

function RunBlock({ run, names }: { run: RunView; names: Record<string, string> }) {
  const finished = run.status === "done" || run.status === "failed";
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? !finished; // 執行中自動展開，完成後自動收起；使用者點過就照使用者的
  const now = useNow(!finished);
  const last = run.steps[run.steps.length - 1];
  const hasProcess = run.status === "planning" || run.steps.length > 0 || run.notes.length > 0;

  let answer = "";
  if (run.status === "done") answer = run.answer ?? "";
  else if (run.source === "direct") answer = run.liveAnswer ?? "";
  else if (last?.status === "running") answer = last.live ?? "";
  const writing = !finished && (run.source === "direct" || last?.status === "running");
  const writer = run.source === "direct" ? "村長" : last ? (names[last.agent] ?? last.agent) : "";

  return (
    <div className="run">
      <div className="bubble user">{run.message}</div>

      <div className="bubble village">
        <div className="row">
          <strong>村長</strong>
          {run.source && <span className="tag">{SOURCE_LABEL[run.source] ?? run.source}</span>}
          <span className={finished ? "muted" : "status-live"}>{statusText(run, names)}</span>
          <span className="muted elapsed">{secs((run.finishedAt ?? now) - run.startedAt)}</span>
        </div>

        {hasProcess && (
          <button className="link" onClick={() => setUserOpen(!open)}>
            {open ? "▾ 執行過程" : "▸ 執行過程"}
            {run.steps.length > 0 && <span className="muted">（{run.steps.length} 步）</span>}
          </button>
        )}
        {hasProcess && open && (
          <div className="process">
            {run.status === "planning" && (
              <div className="pnote live">🤔 村長正在閱讀村民名冊，分派工作…</div>
            )}
            {run.notes.map((n, i) => (
              <div key={i} className="pnote">
                ・{n}
              </div>
            ))}
            {run.steps.map((s) => (
              <StepItem key={s.id} step={s} names={names} now={now} isLast={s === last} />
            ))}
          </div>
        )}

        {(answer || writing) && (
          <div className="answer">
            {answer || <span className="muted">✍️ {writer} 正在寫…</span>}
            {writing && <span className="cursor">▌</span>}
          </div>
        )}
        {run.status === "done" && !run.answer && <div className="step-error">沒有產生結果，請展開執行過程查看原因。</div>}
        {run.status === "failed" && <div className="step-error">執行失敗：{run.error}</div>}
      </div>
    </div>
  );
}

function StepItem({ step: s, names, now, isLast }: { step: StepView; names: Record<string, string>; now: number; isLast: boolean }) {
  const text = s.status === "running" ? s.live : s.output;
  const elapsed = s.elapsed_ms ?? (s.status === "running" && s.startedAt ? now - s.startedAt : undefined);
  return (
    <div className={`pstep ${s.status}`}>
      <div className="row">
        <span>{STATUS_ICON[s.status]}</span>
        <strong>{names[s.agent] ?? s.agent}</strong>
        {s.use.length > 0 && <span className="muted">使用 {s.use.join(", ")} 的成果</span>}
        {s.retries > 0 && <span className="tag warn">重做 {s.retries}</span>}
        {s.model && <span className="tag">{s.model}</span>}
        {elapsed !== undefined && <span className="muted elapsed">{secs(elapsed)}</span>}
      </div>
      <div className="muted">
        <code>{s.id}</code> 指示：{s.instruction}
      </div>
      {s.retryReason && s.status === "running" && <div className="muted">↻ 重做原因：{s.retryReason}</div>}
      {s.warnings.map((w) => (
        <div key={w} className="tag warn">
          {w}
        </div>
      ))}
      {/* 最後一步的成果就是下方的回答，這裡不重複顯示 */}
      {!isLast && (text || s.status === "running") && (
        <pre className="output">
          {text}
          {s.status === "running" && <span className="cursor">▌</span>}
        </pre>
      )}
      {isLast && s.status !== "pending" && s.status !== "error" && <div className="muted">→ 成果顯示在下方回答</div>}
      {s.error && <div className="step-error">{s.error}</div>}
    </div>
  );
}
