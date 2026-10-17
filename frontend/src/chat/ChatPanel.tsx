import { useEffect, useRef, useState } from "react";
import type { AgentInfo, ConversationRow } from "../api";
import { Attachments } from "../ui/Attachments";
import { leafIds, type RunsState, type RunView, type StepView } from "./runs";

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
  onCancel: (runId: string) => Promise<void>;
  onOpenTrace: (runId: string) => void;
  onOpenLlm: () => void;
  conversations: ConversationRow[];
  currentConversation: string | null;
  onOpenConversation: (id: string) => void;
  onRenameConversation: (id: string, title: string) => Promise<void>;
  onDeleteConversation: (id: string) => Promise<void>;
}

export function ChatPanel({
  runs,
  agents,
  onSend,
  onReset,
  onCancel,
  onOpenTrace,
  onOpenLlm,
  conversations,
  currentConversation,
  onOpenConversation,
  onRenameConversation,
  onDeleteConversation,
}: Props) {
  const [listOpen, setListOpen] = useState(false);
  const current = conversations.find((c) => c.id === currentConversation);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const log = useRef<HTMLDivElement>(null);
  const stick = useRef(true); // 使用者往上捲去看舊內容時，串流不要把畫面拉回底部
  const names: Record<string, string> = { mayor: "村長", reviewer: "審核員", ...Object.fromEntries(agents.map((a) => [a.id, a.name])) };
  const active = runs.order.filter((id) => ["planning", "running"].includes(runs.byId[id].status));
  const busy = active.length > 0;

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
        <button className="link conv-toggle" onClick={() => setListOpen(!listOpen)} title="對話紀錄">
          {listOpen ? "▾" : "▸"} 📂 {current ? current.title : "新對話"}
        </button>
        <span style={{ flex: 1 }} />
        <button
          className="link"
          onClick={() => {
            reset();
            setListOpen(false);
          }}
          disabled={!currentConversation && runs.order.length === 0}
          title="開始新的對話（村長會記得同一段對話裡前幾輪的內容）"
        >
          ＋ 新對話
        </button>
      </div>
      {listOpen && (
        <div className="conv-list">
          {conversations.length === 0 && <div className="muted">還沒有對話紀錄。</div>}
          {conversations.map((c) => (
            <div key={c.id} className={`conv-row ${c.id === currentConversation ? "active" : ""}`}>
              <button
                className="conv-open"
                onClick={() => {
                  onOpenConversation(c.id);
                  setListOpen(false);
                }}
              >
                <span className="clamp">{c.title}</span>
                <span className="muted">
                  {Math.ceil(c.messages / 2)} 輪・{new Date(c.updated_at).toLocaleString("zh-TW", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                </span>
              </button>
              <button
                className="link"
                title="改名"
                onClick={() => {
                  const t = prompt("對話名稱", c.title);
                  if (t?.trim()) onRenameConversation(c.id, t.trim()).catch((e) => setError(e.message));
                }}
              >
                ✏️
              </button>
              <button
                className="link danger"
                title="刪除這段對話"
                onClick={() => confirm(`刪除對話「${c.title}」？（執行紀錄會保留）`) && onDeleteConversation(c.id).catch((e) => setError(e.message))}
              >
                🗑
              </button>
            </div>
          ))}
        </div>
      )}

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
          <RunBlock key={id} run={runs.byId[id]} names={names} onOpenTrace={() => onOpenTrace(id)} />
        ))}
      </div>

      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        {error && (
          <p className="error">
            {error}
            {error.includes("LLM 設定") && (
              <button type="button" className="link" onClick={onOpenLlm} style={{ marginLeft: 8, color: "#fff" }}>
                去設定 →
              </button>
            )}
          </p>
        )}
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
        <div className="row">
          <button type="submit" disabled={sending || !text.trim()} style={{ flex: 1 }}>
            {sending ? "送出中…" : busy ? "送出（村民忙碌中）" : "送出"}
          </button>
          {busy && (
            <button
              type="button"
              className="danger"
              title="停止執行中的問答（已完成的步驟會保留在紀錄裡）"
              onClick={() => Promise.all(active.map(onCancel)).catch((e) => setError((e as Error).message))}
            >
              ■ 停止
            </button>
          )}
        </div>
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
  if (run.status === "cancelled") return "已停止";
  if (run.status === "done") return run.fromHistory ? "" : run.ok === false ? "部分失敗" : "完成";
  if (run.reviewing) return "審核員檢查中…";
  if (run.source === "direct") return "回覆中…";
  const working = run.steps.filter((s) => s.status === "running");
  if (working.length) return `${working.map((s) => names[s.agent] ?? s.agent).join("、")} 工作中…`;
  return run.steps.some((s) => s.status === "pending") ? "交接中…" : "整理結果中…";
}

function RunBlock({ run, names, onOpenTrace }: { run: RunView; names: Record<string, string>; onOpenTrace: () => void }) {
  const finished = run.status === "done" || run.status === "failed" || run.status === "cancelled";
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? !finished; // 執行中自動展開，完成後自動收起；使用者點過就照使用者的
  const now = useNow(!finished);
  const leaves = leafIds(run);
  // 只有一個最終步驟時，它的成果就是下方的回答（串流也顯示在那裡）；平行有多個時各自顯示在執行過程
  const last = leaves.size === 1 ? run.steps.find((s) => leaves.has(s.id)) : undefined;
  const lastReview = run.reviews[run.reviews.length - 1];
  const hasProcess = run.status === "planning" || run.steps.length > 0 || run.notes.length > 0 || run.reviews.length > 0;

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
          {!run.fromHistory && <span className="muted elapsed">{secs((run.finishedAt ?? now) - run.startedAt)}</span>}
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
            {run.reviews.map((r, i) => (
              <div key={i} className={`pnote review ${r.skipped ? "" : r.passed ? "ok" : "bad"}`}>
                🔍 審核員：{r.skipped ? r.reason : r.passed ? `合格。${r.reason}` : `退回 ${r.step ?? ""}。${r.reason}`}
              </div>
            ))}
            {run.reviewing && <div className="pnote live">🔍 審核員正在檢查答案…</div>}
          </div>
        )}

        {(answer || writing) && (
          <div className="answer">
            {answer || <span className="muted">✍️ {writer} 正在寫…</span>}
            {writing && <span className="cursor">▌</span>}
          </div>
        )}
        {run.status === "done" && <Attachments items={run.attachments} />}
        {run.status === "done" && !run.answer && <div className="step-error">沒有產生結果，請展開執行過程查看原因。</div>}
        {run.status === "failed" && <div className="step-error">執行失敗：{run.error}</div>}
        {run.status === "cancelled" && <div className="muted">■ 已停止。完成的步驟保留在執行過程與紀錄裡。</div>}
        {run.scribing && <div className="muted scribe-note">📝 書記正在整理要記住的事…</div>}
        {run.remembered?.map((m) => (
          <div key={m} className="scribe-note" title="可以在「記憶」頁修改或刪除">
            📝 書記記下：{m}
          </div>
        ))}
        {run.forgotten?.map((m) => (
          <div key={m} className="scribe-note muted">
            🗑 書記忘掉：{m}
          </div>
        ))}
        {finished && (
          <div className="row answer-foot">
            {lastReview && !lastReview.skipped && (
              <span className={`tag ${lastReview.passed ? "ok" : "warn"}`} title={lastReview.reason}>
                {lastReview.passed ? "🔍 審核通過" : "🔍 審核有疑慮"}
              </span>
            )}
            <span style={{ flex: 1 }} />
            {/* 從紀錄載入但當時沒有回答的那一輪，id 是訊息 id，沒有對應的執行紀錄 */}
            {!(run.fromHistory && !/^[0-9a-f]{12}$/.test(run.id)) && (
              <button className="link" onClick={onOpenTrace} title="查看每一步的輸入、輸出、耗時與 token">
                📜 紀錄
              </button>
            )}
          </div>
        )}
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
      {!isLast && <Attachments items={s.attachments} />}
      {isLast && s.status !== "pending" && s.status !== "error" && <div className="muted">→ 成果顯示在下方回答</div>}
      {s.error && <div className="step-error">{s.error}</div>}
    </div>
  );
}
