import { useEffect, useState } from "react";
import { llm, rules as rulesApi, type Model, type VillageRules } from "../api";
import { Field, NumInput } from "../villagers/fields";

/** 村規：整個村子的運作方式（審核員、逾時、平行執行、紀錄保留），存 DB，下一次問答就生效 */
export function RulesPanel() {
  const [saved, setSaved] = useState<VillageRules | null>(null);
  const [draft, setDraft] = useState<VillageRules | null>(null);
  const [models, setModels] = useState<Model[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    Promise.all([rulesApi.get(), llm.models()])
      .then(([r, m]) => {
        setSaved(r);
        setDraft(r);
        setModels(m);
      })
      .catch((e) => setError(e.message));
  }, []);

  if (!draft || !saved) return <div className="panel-body">{error ? <p className="error">{error}</p> : <p className="muted">載入中…</p>}</div>;

  const set = (patch: Partial<VillageRules>) => {
    setDraft({ ...draft, ...patch });
    setNotice("");
  };
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);

  return (
    <div className="panel-body spec-form">
      {error && (
        <p className="error" onClick={() => setError("")}>
          {error}
        </p>
      )}

      <section>
        <h3>🔍 審核員</h3>
        <p className="hint">答案交給你之前，審核員會檢查：有沒有做到你要求的事、有沒有違反你明確的要求（語言、形式、數量）、內容是否恰當。不合格就把理由交給負責的村民重做。</p>
        <label className="toggle">
          <input type="checkbox" checked={draft.reviewer_enabled} onChange={(e) => set({ reviewer_enabled: e.target.checked })} />
          啟用審核員
        </label>
        {draft.reviewer_enabled && (
          <div className="grid2">
            <Field label="審核員用的模型" hint="判斷需要理解力，建議 large；每次問答會多一次 LLM 呼叫">
              <select value={draft.reviewer_target} onChange={(e) => set({ reviewer_target: e.target.value })}>
                <option value="tier:large">large 分級</option>
                <option value="tier:small">small 分級</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    指定：{m.provider_name} / {m.display_name || m.model}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="不合格時退回重做" hint="重做後還是不合格，就照實交出並標示「審核有疑慮」">
              <select value={draft.review_redo} onChange={(e) => set({ review_redo: Number(e.target.value) })}>
                <option value={0}>不重做，只標示</option>
                <option value={1}>1 次</option>
                <option value={2}>2 次</option>
              </select>
            </Field>
          </div>
        )}
      </section>

      <section>
        <h3>⚙️ 執行</h3>
        <label className="toggle">
          <input type="checkbox" checked={draft.parallel} onChange={(e) => set({ parallel: e.target.checked })} />
          平行執行：互不依賴的步驟同時進行
        </label>
        <p className="hint">例如「把這句話翻成英文，也講一個相關的笑話」，兩位村民可以同時工作。本地模型一次只能處理一個請求的話，開了也只是排隊，不會出錯。</p>
        <div className="grid2">
          {draft.parallel && (
            <Field label="最多同時幾位村民" hint="1～8">
              <NumInput value={draft.max_parallel} min={1} max={8} onChange={(v) => set({ max_parallel: v ?? 3 })} />
            </Field>
          )}
          <Field label="每一步的時間上限（秒）" hint="超過就判定這一步失敗，避免卡住整個問答；10～1800。思考型或本地模型可以設長一點">
            <NumInput value={draft.step_timeout_s} min={10} max={1800} step={10} onChange={(v) => set({ step_timeout_s: v ?? 180 })} />
          </Field>
        </div>
      </section>

      <section>
        <h3>💬 對話</h3>
        <Field label="問答時帶入前幾輪對話" hint="村長、村民與審核員會看到同一段對話的前幾輪，才聽得懂「再把它翻成英文」這類接續的話。0 = 每次都當成新問題；越多越懂前文，但每次用的 token 也越多">
          <NumInput value={draft.context_turns} min={0} max={10} onChange={(v) => set({ context_turns: v ?? 3 })} />
        </Field>
      </section>

      <section>
        <h3>📜 執行紀錄</h3>
        <Field label="保留最近幾筆" hint="超過的舊紀錄會自動刪除；10～10000">
          <NumInput value={draft.keep_runs} min={10} max={10000} step={100} onChange={(v) => set({ keep_runs: v ?? 500 })} />
        </Field>
      </section>

      <div className="row">
        <button
          disabled={!dirty}
          onClick={async () => {
            try {
              const r = await rulesApi.save(draft);
              setSaved(r);
              setDraft(r);
              setNotice("已儲存，下一次問答就會套用");
            } catch (e) {
              setError((e as Error).message);
            }
          }}
        >
          儲存村規
        </button>
        {dirty && (
          <button className="link" onClick={() => setDraft(saved)}>
            還原
          </button>
        )}
        {dirty ? <span className="hint">● 有未儲存的變更</span> : notice && <span className="hint">{notice}</span>}
      </div>
    </div>
  );
}
