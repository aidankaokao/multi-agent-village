import { useEffect, useRef, useState } from "react";
import type { AgentInfo, ConversationRow } from "../api";
import { Attachments, DocChips, ImageStrip } from "../ui/Attachments";
import { Markdown } from "../ui/Markdown";
import { acceptedExts, docAgents, pickDocs, readDoc, type DocFile } from "./docs";
import { findMention, insertMention, isAway, matchAgents, type MentionQuery } from "./mention";
import { imageFiles, prepareImage } from "./prepareImage";
import { leafIds, type AppraisalView, type LoopVerdictView, type RunsState, type RunView, type StepView } from "./runs";

/** 能不能附圖：要有通過看圖測驗的視覺模型；張數上限由村規決定 */
export interface Vision {
  ready: boolean;
  maxImages: number;
}

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
  onSend: (message: string, images: string[], docs: DocFile[]) => Promise<void>;
  vision: Vision;
  prefill?: { text: string; nonce: number } | null; // 點地圖上的村民：輸入框預先填好「@id 」
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
  vision,
  prefill,
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
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (!prefill) return;
    setText(prefill.text);
    // 問答視窗剛打開時還沒顯示，等下一個畫面再把游標放到最後
    requestAnimationFrame(() => {
      const el = input.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(prefill.text.length, prefill.text.length);
    });
  }, [prefill]);
  const [sending, setSending] = useState(false);
  const [mention, setMention] = useState<MentionQuery | null>(null); // 正在打的 @xxx
  const [mentionAt, setMentionAt] = useState(0); // 清單裡目前選到第幾個
  const choices = mention ? matchAgents(agents, mention.query) : [];
  const menuOpen = choices.length > 0;
  const syncMention = (el: HTMLTextAreaElement) => {
    const m = el.selectionStart === el.selectionEnd ? findMention(el.value, el.selectionStart) : null;
    setMention((old) => {
      if (!m || old?.start !== m.start || old.query !== m.query) setMentionAt(0);
      return m;
    });
  };
  const pickMention = (a: AgentInfo) => {
    if (!mention || isAway(a)) return;
    const next = insertMention(text, mention, a.id);
    setText(next.text);
    setMention(null);
    requestAnimationFrame(() => {
      const el = input.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(next.caret, next.caret);
    });
  };
  const [error, setError] = useState("");
  const [images, setImages] = useState<string[]>([]); // 準備附上的圖（已縮圖的 data URL）
  const [preparing, setPreparing] = useState(false);
  const [docs, setDocs] = useState<DocFile[]>([]); // 準備附上的文件（只交給收文件的駐村專家）
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const docInput = useRef<HTMLInputElement>(null);
  const docExts = acceptedExts(agents);
  const docHelpers = docAgents(agents).map((a) => a.name).join("、");
  const log = useRef<HTMLDivElement>(null);
  const stick = useRef(true); // 使用者往上捲去看舊內容時，串流不要把畫面拉回底部
  const names: Record<string, string> = { mayor: "村長", reviewer: "審核員", appraiser: "鑑定師", ...Object.fromEntries(agents.map((a) => [a.id, a.name])) };
  const active = runs.order.filter((id) => ["planning", "running"].includes(runs.byId[id].status));
  const busy = active.length > 0;

  useEffect(() => {
    const el = log.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [runs]);

  const attachHint = !vision.ready
    ? "附圖需要視覺模型：到「LLM 設定」對模型做看圖測驗，通過後加入 vision 分級"
    : vision.maxImages === 0
      ? "村規目前不開放附圖"
      : `附圖（最多 ${vision.maxImages} 張；也可以直接貼上或拖曳進來）`;

  const docHint = docExts.length
    ? `附文件（${docExts.join("、")}，最多 3 份）：交給${docHelpers}整理`
    : "附文件需要「在村」的文件彙整師這類駐村專家（管理員到村民頁邀請；外出中的也不行）";

  const addDocs = async (files: File[]) => {
    if (!files.length) return;
    if (!docExts.length) return setError(docHint);
    const { ok, error: why } = pickDocs(files, docs, docExts);
    setError(why);
    try {
      const read = await Promise.all(ok.map(readDoc));
      setDocs((xs) => [...xs, ...read]);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  /** 拖曳 / 貼上：圖片給鑑定師、其他檔案當文件 */
  const addAny = (files: File[]) => {
    const pics = files.filter((f) => f.type.startsWith("image/"));
    const others = files.filter((f) => !f.type.startsWith("image/"));
    if (pics.length) addFiles(pics);
    if (others.length) addDocs(others);
  };

  const addFiles = async (files: File[]) => {
    if (!files.length) return;
    if (!vision.ready || vision.maxImages === 0) return setError(attachHint);
    const room = vision.maxImages - images.length;
    if (room <= 0) return setError(`一次最多附 ${vision.maxImages} 張圖`);
    setPreparing(true);
    setError(files.length > room ? `一次最多附 ${vision.maxImages} 張圖，只加入前 ${room} 張` : "");
    try {
      const ready = await Promise.all(files.slice(0, room).map(prepareImage));
      setImages((xs) => [...xs, ...ready].slice(0, vision.maxImages));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPreparing(false);
    }
  };

  const send = async (message: string) => {
    const m = message.trim();
    if ((!m && !images.length && !docs.length) || sending || preparing) return;
    setSending(true);
    setError("");
    stick.current = true;
    try {
      await onSend(m, images, docs);
      setText("");
      setImages([]);
      setDocs([]);
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
    <div
      className={`chat ${dragging ? "dragging" : ""}`}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => e.currentTarget === e.target && setDragging(false)}
      onDrop={(e) => {
        if (!e.dataTransfer.files.length) return;
        e.preventDefault();
        setDragging(false);
        addAny([...e.dataTransfer.files]);
      }}
    >
      {dragging && (
        <div className="drop-hint">
          {[vision.ready && vision.maxImages > 0 ? "圖片" : "", docExts.length ? `文件（${docExts.join("、")}）` : ""].filter(Boolean).join("或") ||
            attachHint}
          {(vision.ready && vision.maxImages > 0) || docExts.length ? "：放開就附上" : ""}
        </div>
      )}
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
        {(images.length > 0 || preparing) && (
          <div className="pending-images">
            {images.map((src, i) => (
              <figure key={i}>
                <img src={src} alt={`附圖 ${i + 1}`} />
                <button type="button" title="移除" onClick={() => setImages((xs) => xs.filter((_, j) => j !== i))}>
                  ✕
                </button>
              </figure>
            ))}
            {preparing && <span className="muted">縮圖中…</span>}
          </div>
        )}
        {docs.length > 0 && <DocChips files={docs} onRemove={(i) => setDocs((xs) => xs.filter((_, j) => j !== i))} />}
        {menuOpen && (
          <div className="mention-menu" role="listbox">
            {choices.map((a, i) => (
              <button
                key={a.id}
                type="button"
                role="option"
                aria-selected={i === mentionAt}
                className={`mention-item ${i === mentionAt ? "active" : ""} ${isAway(a) ? "away" : ""}`}
                disabled={isAway(a)}
                onMouseDown={(e) => e.preventDefault()} // 不要讓輸入框失去焦點
                onMouseEnter={() => setMentionAt(i)}
                onClick={() => pickMention(a)}
              >
                <strong>{a.name}</strong>
                <code>@{a.id}</code>
                {isAway(a) && <span className="tag warn">外出中</span>}
                {a.kind === "remote" && !isAway(a) && <span className="tag">🧳 駐村專家</span>}
                <span className="muted clamp">{a.description}</span>
              </button>
            ))}
            <div className="mention-hint muted">↑↓ 選擇・Enter / Tab 確定・Esc 關閉</div>
          </div>
        )}
        <textarea
          ref={input}
          rows={3}
          value={text}
          placeholder={
            docs.length
              ? "報告的重點方向，例如「著重各分店的比較」（留空 = 請彙整這些文件的重點）"
              : images.length
              ? "問問關於這張圖的問題（留空 = 這張圖片裡有什麼？）"
              : "輸入問題，Enter 送出、Shift+Enter 換行；可用 @poet、@jester 直接指名"
          }
          onChange={(e) => {
            setText(e.target.value);
            syncMention(e.target);
          }}
          onSelect={(e) => syncMention(e.currentTarget)}
          onBlur={() => setTimeout(() => setMention(null), 150)}
          onPaste={(e) => {
            const files = [...e.clipboardData.files];
            if (files.length) {
              e.preventDefault();
              addAny(files);
            }
          }}
          onKeyDown={(e) => {
            if (menuOpen && !e.nativeEvent.isComposing) {
              const n = choices.length;
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                setMentionAt((i) => (i + (e.key === "ArrowDown" ? 1 : n - 1)) % n);
                return;
              }
              if (e.key === "Enter" || e.key === "Tab") {
                e.preventDefault();
                pickMention(choices[Math.min(mentionAt, n - 1)]);
                return;
              }
              if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation(); // 只關清單，不要連問答視窗一起關
                e.nativeEvent.stopImmediatePropagation();
                setMention(null);
                return;
              }
            }
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send(text);
            }
          }}
        />
        <div className="row">
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(e) => {
              addFiles(imageFiles(e.target.files));
              e.target.value = ""; // 同一張圖可以再選一次
            }}
          />
          <button
            type="button"
            className="attach"
            title={attachHint}
            disabled={!vision.ready || vision.maxImages === 0 || preparing}
            onClick={() => fileInput.current?.click()}
          >
            🖼️
          </button>
          <input
            ref={docInput}
            type="file"
            accept={docExts.join(",")}
            multiple
            hidden
            onChange={(e) => {
              addDocs([...(e.target.files ?? [])]);
              e.target.value = "";
            }}
          />
          <button type="button" className="attach" title={docHint} disabled={!docExts.length} onClick={() => docInput.current?.click()}>
            📎
          </button>
          <button type="submit" disabled={sending || preparing || (!text.trim() && !images.length && !docs.length)} style={{ flex: 1 }}>
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
  if (run.appraisal?.status === "running") return run.appraisal.relook ? "鑑定師重看前面的圖…" : "鑑定師看圖中…";
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
  const hasProcess =
    run.status === "planning" || run.steps.length > 0 || run.notes.length > 0 || run.reviews.length > 0 || !!run.appraisal;

  let answer = "";
  if (run.status === "done") answer = run.answer ?? "";
  else if (run.source === "direct") answer = run.liveAnswer ?? "";
  else if (last?.status === "running") answer = last.live ?? "";
  const writing = !finished && (run.source === "direct" || last?.status === "running");
  const writer = run.source === "direct" ? "村長" : last ? (names[last.agent] ?? last.agent) : "";

  return (
    <div className="run">
      <div className="bubble user">
        {run.images && run.images.length > 0 && <ImageStrip urls={run.images} />}
        <DocChips files={run.files} />
        {run.message}
      </div>

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
            {run.appraisal && <AppraisalNote a={run.appraisal} />}
            {run.status === "planning" && run.appraisal?.status !== "running" && (
              <div className="pnote live">🤔 村長正在閱讀村民名冊，分派工作…</div>
            )}
            {run.notes.map((n, i) => (
              <div key={i} className="pnote">
                ・{n}
              </div>
            ))}
            {run.loop && (
              <div className="pnote">
                🔁 {names[run.steps.find((x) => x.id === run.loop!.check)?.agent ?? ""] ?? run.loop.check} 審查（{run.loop.check}），不通過就退回{" "}
                {names[run.steps.find((x) => x.id === run.loop!.back_to)?.agent ?? ""] ?? run.loop.back_to}（{run.loop.back_to}）重做
                {run.loop.max_rounds ? `，最多 ${run.loop.max_rounds} 輪` : ""}
              </div>
            )}
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
            {answer ? <Markdown text={answer} /> : <span className="muted">✍️ {writer} 正在寫…</span>}
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

/** 迴圈的審查結論：通過、不通過退回重做、或用完輪數交出最後一版 */
function VerdictNote({ v, names }: { v: LoopVerdictView; names: Record<string, string> }) {
  const back = names[v.backAgent] ?? v.backAgent;
  const how = v.how && v.how !== "結論" ? `（${v.how}）` : "";
  if (v.passed) return <div className="pnote review ok">✅ 通過（第 {v.round} 輪）{how}</div>;
  return (
    <div className="pnote review bad">
      {v.retry
        ? `❌ 不通過 → 退回${back}重做（第 ${v.round}/${v.maxRounds} 輪）${how}`
        : `❌ 不通過，但${v.stop ?? "輪數用完"}，交出最後一版${how}`}
      {v.feedback && <div className="muted loop-feedback">{v.feedback}</div>}
    </div>
  );
}

function AppraisalNote({ a }: { a: AppraisalView }) {
  const what = `${a.relook ? "重看前面附的" : "看了"} ${a.count} 張圖`;
  if (a.status === "running") return <div className="pnote live">🔎 鑑定師正在{a.relook ? "重看前面附的" : "看"} {a.count} 張圖…</div>;
  if (a.status === "error") return <div className="pnote review bad">🔎 鑑定師沒辦法看圖：{a.error}</div>;
  return (
    <details className="pnote appraisal">
      <summary>
        🔎 鑑定師{what}
        {a.model && <span className="tag">{a.model}</span>}
        {a.elapsed_ms !== undefined && <span className="muted elapsed">{secs(a.elapsed_ms)}</span>}
      </summary>
      <pre className="output">{a.text}</pre>
    </details>
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
        {(s.round ?? 1) > 1 && <span className="tag">第 {s.round} 輪</span>}
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
        <div className="output">
          {text && <Markdown text={text} />}
          {s.status === "running" && <span className="cursor">▌</span>}
        </div>
      )}
      {!isLast && <Attachments items={s.attachments} />}
      {s.verdict && <VerdictNote v={s.verdict} names={names} />}
      {s.rounds && s.rounds.length > 0 && (
        <details className="loop-rounds">
          <summary className="muted">前 {s.rounds.length} 輪的版本</summary>
          {s.rounds.map((r) => (
            <div key={r.round}>
              <div className="muted">第 {r.round} 輪{r.verdict ? `：${r.verdict.passed ? "通過" : "不通過"}` : ""}</div>
              {r.output && (
                <div className="output">
                  <Markdown text={r.output} />
                </div>
              )}
            </div>
          ))}
        </details>
      )}
      {isLast && s.status !== "pending" && s.status !== "error" && <div className="muted">→ 成果顯示在下方回答</div>}
      {s.error && <div className="step-error">{s.error}</div>}
    </div>
  );
}
