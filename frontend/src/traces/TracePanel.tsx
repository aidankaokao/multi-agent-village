import { useCallback, useEffect, useState } from "react";
import { Attachments } from "../ui/Attachments";
import { traces, type AgentInfo, type RunDetail, type RunStatus, type RunSummary, type TraceStep } from "../api";

const STATUS: Record<RunStatus, [string, string]> = {
  running: ["⏳", "執行中"],
  done: ["✅", "完成"],
  error: ["❌", "有錯誤"],
  cancelled: ["■", "已停止"],
  interrupted: ["⚠️", "中斷"],
};

const SOURCE_LABEL: Record<string, string> = { llm: "村長規劃", mention: "@指名", keyword: "關鍵字備援", direct: "村長直接回覆" };

const secs = (ms: number | null | undefined) => (ms == null ? "—" : `${(ms / 1000).toFixed(1)}s`);
const tokens = (i: number, o: number) => (i || o ? `${(i + o).toLocaleString()} tokens` : "");

function when(iso: string): string {
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "剛剛";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分鐘前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`;
  return d.toLocaleString("zh-TW", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** 執行紀錄：每次問答的計畫、每一步的輸入輸出、審核結果、耗時與 token */
export function TracePanel({
  agents,
  openId,
  onOpened,
  refreshKey,
}: {
  agents: AgentInfo[];
  openId: string | null; // 從問答點「📜 紀錄」跳過來時要打開的那筆
  onOpened: () => void;
  refreshKey: number; // 有問答結束就 +1，列表自動更新
}) {
  const names: Record<string, string> = { mayor: "村長", reviewer: "審核員", ...Object.fromEntries(agents.map((a) => [a.id, a.name])) };
  const [list, setList] = useState<RunSummary[]>([]);
  const [more, setMore] = useState(false);
  const [q, setQ] = useState("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async (query: string) => {
    try {
      const rows = await traces.list({ q: query, limit: 30 });
      setList(rows);
      setMore(rows.length === 30);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    const t = window.setTimeout(() => load(q), 250); // 打字時稍等一下再搜尋
    return () => window.clearTimeout(t);
  }, [q, refreshKey, load]);

  const open = useCallback(async (id: string) => {
    try {
      setDetail(await traces.get(id));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    if (openId) {
      open(openId);
      onOpened();
    }
  }, [openId, open, onOpened]);

  // 正在看的那筆如果剛跑完，重新載入
  useEffect(() => {
    if (detail?.status === "running") open(detail.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  if (detail) return <Detail run={detail} names={names} onBack={() => setDetail(null)} onDeleted={() => (setDetail(null), load(q))} />;

  return (
    <div className="panel-body">
      {error && (
        <p className="error" onClick={() => setError("")}>
          {error}
        </p>
      )}
      <div className="row">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜尋問題內容" style={{ flex: 1 }} />
        <button
          className="link danger"
          disabled={list.length === 0}
          onClick={async () => {
            if (!confirm("清空所有執行紀錄？（執行中的不會刪）")) return;
            await traces.clear().catch((e) => setError(e.message));
            load(q);
          }}
        >
          清空紀錄
        </button>
      </div>
      {list.length === 0 && <p className="hint">{q ? "沒有符合的紀錄。" : "還沒有紀錄。到「問答」問村民一個問題，這裡就會出現每一步的細節。"}</p>}
      {list.map((r) => (
        <div key={r.id} className="card list-row trace-row" onClick={() => open(r.id)}>
          <span title={STATUS[r.status][1]}>{STATUS[r.status][0]}</span>
          <div className="list-main">
            <div className="clamp">{r.message}</div>
            <div className="muted">
              {r.user && <>👤 {r.user}・</>}
              {r.agents.length ? r.agents.map((a) => names[a] ?? a).join(" → ") : "村長直接回覆"}
              {r.review !== null && (r.review ? "・🔍 通過" : "・🔍 有疑慮")}
            </div>
          </div>
          <div className="trace-meta muted">
            <div>{when(r.created_at)}</div>
            <div>
              {secs(r.elapsed_ms)} {tokens(r.tokens_in, r.tokens_out) && `・${tokens(r.tokens_in, r.tokens_out)}`}
            </div>
          </div>
        </div>
      ))}
      {more && (
        <button
          className="link"
          onClick={async () => {
            const rows = await traces.list({ q, limit: 30, before: list[list.length - 1].created_at });
            setList((l) => [...l, ...rows]);
            setMore(rows.length === 30);
          }}
        >
          載入更多
        </button>
      )}
    </div>
  );
}

function Detail({ run, names, onBack, onDeleted }: { run: RunDetail; names: Record<string, string>; onBack: () => void; onDeleted: () => void }) {
  const name = (id: string) => names[id] ?? id;
  return (
    <div className="panel-body trace">
      <div className="editor-head">
        <button className="link" onClick={onBack}>
          ← 紀錄列表
        </button>
        <span style={{ flex: 1 }} />
        <button
          className="link danger"
          onClick={async () => {
            if (confirm("刪除這筆紀錄？")) {
              await traces.remove(run.id);
              onDeleted();
            }
          }}
        >
          刪除
        </button>
      </div>

      <div className="bubble user" style={{ alignSelf: "stretch", maxWidth: "none" }}>
        {run.message}
      </div>
      <div className="row muted">
        <span>
          {STATUS[run.status][0]} {STATUS[run.status][1]}
        </span>
        <span>・{new Date(run.created_at).toLocaleString("zh-TW")}</span>
        <span>・總耗時 {secs(run.elapsed_ms)}</span>
        {tokens(run.tokens_in, run.tokens_out) && (
          <span title={`輸入 ${run.tokens_in}、輸出 ${run.tokens_out}`}>・{tokens(run.tokens_in, run.tokens_out)}</span>
        )}
      </div>

      {run.plan?.appraisal && (
        <section>
          <h3>
            🔎 鑑定師看圖（{run.plan.appraisal.count} 張{run.plan.appraisal.relook ? "，追問時重看前面的圖" : ""}）
          </h3>
          <div className="row muted">
            {run.plan.appraisal.model && <span className="tag">{run.plan.appraisal.model}</span>}
            <span>{secs(run.plan.appraisal.elapsed_ms)}</span>
            {tokens(run.plan.appraisal.tokens_in, run.plan.appraisal.tokens_out) && (
              <span>・{tokens(run.plan.appraisal.tokens_in, run.plan.appraisal.tokens_out)}</span>
            )}
          </div>
          {run.plan.appraisal.ok ? (
            <pre className="output">{run.plan.appraisal.text}</pre>
          ) : (
            <div className="step-error">沒辦法看圖：{run.plan.appraisal.error}</div>
          )}
        </section>
      )}

      {run.plan && (
        <section>
          <h3>① 村長的計畫（{SOURCE_LABEL[run.plan.source] ?? run.plan.source}）</h3>
          {run.plan.notes.map((n, i) => (
            <div key={i} className="pnote">
              ・{n}
            </div>
          ))}
          {run.plan.steps.length === 0 && <div className="muted">沒有派工，由村長直接回覆。</div>}
          {run.plan.steps.map((s) => (
            <div key={s.id} className="muted">
              <code>{s.id}</code> {name(s.agent)}：{s.instruction}
              {s.use.length > 0 && `（使用 ${s.use.join(", ")} 的成果）`}
            </div>
          ))}
        </section>
      )}

      {run.steps.length > 0 && (
        <section>
          <h3>② 每一步</h3>
          {run.steps.map((s) => (
            <StepCard key={s.id} s={s} name={name(s.agent)} />
          ))}
        </section>
      )}

      {run.reviews.length > 0 && (
        <section>
          <h3>③ 審核員</h3>
          {run.reviews.map((r, i) => (
            <div key={i} className={`card review-card ${r.passed ? "ok" : "bad"}`}>
              <div className="row">
                <strong>
                  第 {i + 1} 次：{r.passed ? "✅ 合格" : `↩ 退回 ${r.step ?? ""}`}
                </strong>
                {r.model && <span className="tag">{r.model}</span>}
                <span className="muted elapsed">
                  {secs(r.elapsed_ms)} {tokens(r.tokens_in, r.tokens_out)}
                </span>
              </div>
              <div>{r.reason}</div>
              {r.fix && <div className="muted">修改建議：{r.fix}</div>}
            </div>
          ))}
        </section>
      )}

      <section>
        <h3>{run.reviews.length ? "④" : run.steps.length ? "③" : "②"} 最終回答</h3>
        {run.answer ? <pre className="output">{run.answer}</pre> : <div className="muted">（沒有回答）</div>}
        {run.error && <div className="step-error">{run.error}</div>}
      </section>
    </div>
  );
}

function StepCard({ s, name }: { s: TraceStep; name: string }) {
  return (
    <div className={`card trace-step ${s.status}`}>
      <div className="row">
        <span>{s.status === "done" ? "✅" : s.status === "error" ? "❌" : "⏳"}</span>
        <strong>{name}</strong>
        <code className="muted">{s.id}</code>
        {s.model && <span className="tag">{s.model}</span>}
        {s.attempts > 1 && <span className="tag warn">共 {s.attempts} 次</span>}
        <span className="muted elapsed">
          {secs(s.elapsed_ms)} {tokens(s.tokens_in, s.tokens_out)}
        </span>
      </div>
      <div className="muted">指示：{s.instruction}</div>
      {s.input && (
        <details>
          <summary className="muted">送給村民的完整訊息</summary>
          <pre className="output">{s.input}</pre>
        </details>
      )}
      {s.retry_reasons.map((r, i) => (
        <div key={i} className="muted">
          ↻ 自動檢查要求重做：{r}
        </div>
      ))}
      {s.rejected.map((r, i) => (
        <details key={i}>
          <summary className="muted">↩ 被審核員退回的版本 {i + 1}：{r.reason}</summary>
          <pre className="output">{r.output}</pre>
        </details>
      ))}
      {s.warnings.map((w) => (
        <span key={w} className="tag warn">
          {w}
        </span>
      ))}
      {s.tool_args && (
        <details>
          <summary className="muted">🔧 交給工具的參數</summary>
          <pre className="output">{JSON.stringify(s.tool_args, null, 2)}</pre>
        </details>
      )}
      {s.output && <pre className="output">{s.output}</pre>}
      <Attachments items={s.attachments} />
      {s.error && <div className="step-error">{s.error}</div>}
    </div>
  );
}
