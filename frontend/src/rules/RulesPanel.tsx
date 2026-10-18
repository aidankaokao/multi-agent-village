import { useEffect, useState } from "react";
import { llm, rules as rulesApi, type Model, type StorageUsage, type VillageRules } from "../api";
import { fmtSize } from "../chat/docs";
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
        <h3>📝 長期記憶（書記）</h3>
        <p className="hint">書記會在問答後記下關於使用者本人、長期有效的事（名字、偏好、生日…），之後的問答會帶入，跨對話也記得。每個人只會用到自己的記憶，可以在「記憶」頁查看與修改。</p>
        <label className="toggle">
          <input type="checkbox" checked={draft.memory_enabled} onChange={(e) => set({ memory_enabled: e.target.checked })} />
          問答時帶入長期記憶
        </label>
        <label className="toggle">
          <input type="checkbox" checked={draft.memory_auto} onChange={(e) => set({ memory_auto: e.target.checked })} />
          書記在問答後自動記錄（只在句子提到自己時才呼叫 LLM，用 small 分級）
        </label>
        <Field label="每次最多帶入幾條記憶" hint="1～30；記憶多時，偏好類一定帶，其他依跟問題的相關程度挑選">
          <NumInput value={draft.memory_max} min={1} max={30} onChange={(v) => set({ memory_max: v ?? 10 })} />
        </Field>
      </section>

      <section>
        <h3>🖼️ 附圖</h3>
        <Field label="每次問答最多附幾張圖" hint="0～10；0 = 不開放附圖。圖片會送給視覺模型，張數越多越花 token">
          <NumInput value={draft.max_images} min={0} max={10} onChange={(v) => set({ max_images: v ?? 3 })} />
        </Field>
      </section>

      <section>
        <h3>🎨 畫家</h3>
        <Field label="每次問答最多畫幾張" hint="0～4；0 = 不開放畫圖。生圖比文字貴很多（每張約 0.01～0.2 美元）">
          <NumInput value={draft.paint_per_run} min={0} max={4} onChange={(v) => set({ paint_per_run: v ?? 1 })} />
        </Field>
        <Field label="每人每天最多畫幾張" hint="以最近 24 小時計算，村民試打與生圖測驗也算在內；0～500">
          <NumInput value={draft.paint_per_day} min={0} max={500} step={5} onChange={(v) => set({ paint_per_day: v ?? 20 })} />
        </Field>
        <span className="hint">每個人用自己的 API key 畫圖。畫作保存 14 天後自動刪除；審核員退件時畫家不會重畫（避免重複計費）。</span>
      </section>

      <section>
        <h3>🎙️ 聽打員</h3>
        <Field label="每份錄音最長幾分鐘" hint="0～600；0 = 不開放附錄音。轉寫按分鐘計費（OpenAI whisper-1 每分鐘約 0.006 美元，一小時約 0.36 美元）">
          <NumInput value={draft.transcribe_max_minutes} min={0} max={600} step={30} onChange={(v) => set({ transcribe_max_minutes: v ?? 180 })} />
        </Field>
        <span className="hint">每個人用自己的 API key 轉寫。錄音轉完就刪除，逐字稿檔案保存 14 天；審核員退件時聽打員不會重轉。</span>
      </section>

      <section>
        <h3>🔁 迴圈（改到過關為止）</h3>
        <Field
          label="最多幾輪"
          hint="1～5。例如「審稿人檢查，不通過就重寫」：審查不通過會退回重做，到這個輪數就交出最後一版。每輪都會再呼叫一次村民的 LLM（駐村專家、畫家也一樣），輪數越多越花錢"
        >
          <NumInput value={draft.loop_max_rounds} min={1} max={5} onChange={(v) => set({ loop_max_rounds: v ?? 3 })} />
        </Field>
      </section>

      <section>
        <h3>🗄️ 儲存空間</h3>
        <Field
          label="使用者附的圖與文件保存幾天"
          hint="0～3650；0 = 跟著對話永久保存。超過天數的附件會被清掉（對話文字還在，只是看不到原圖、追問時不能再交給駐村專家）"
        >
          <NumInput value={draft.attachment_keep_days} min={0} max={3650} step={30} onChange={(v) => set({ attachment_keep_days: v ?? 90 })} />
        </Field>
        <Storage onError={setError} />
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

/** 儲存空間用量：各類檔案占多少、資料庫檔案多大；可以立即清理 */
function Storage({ onError }: { onError: (m: string) => void }) {
  const [usage, setUsage] = useState<StorageUsage | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState("");
  useEffect(() => {
    rulesApi.storage().then(setUsage).catch((e) => onError(e.message));
  }, [onError]);
  if (!usage) return <p className="muted">讀取用量中…</p>;
  const total = usage.items.reduce((n, x) => n + x.bytes, 0);
  return (
    <div className="storage">
      <table className="storage-table">
        <tbody>
          {usage.items.map((x) => (
            <tr key={x.key}>
              <td>{x.label}</td>
              <td className="muted">{x.count} 個</td>
              <td>{fmtSize(x.bytes)}</td>
            </tr>
          ))}
          {usage.items.length === 0 && (
            <tr>
              <td className="muted">目前沒有任何檔案</td>
            </tr>
          )}
          <tr>
            <td>
              <strong>檔案合計</strong>
            </td>
            <td />
            <td>
              <strong>{fmtSize(total)}</strong>
            </td>
          </tr>
          {usage.db_bytes != null && (
            <tr>
              <td>資料庫檔案</td>
              <td className="muted">{usage.free_bytes ? `其中 ${fmtSize(usage.free_bytes)} 空著` : ""}</td>
              <td>{fmtSize(usage.db_bytes)}</td>
            </tr>
          )}
        </tbody>
      </table>
      <span className="hint">
        畫作、報告與逐字稿保存 14 天，錄音轉完就刪，每 6 小時自動清理；清掉的空間很多時會自動壓縮資料庫檔案。
        「立即清理」會依上面<strong>已儲存</strong>的天數清掉太舊的附件，並壓縮資料庫。
      </span>
      <div className="row">
        <button
          type="button"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setDone("");
            try {
              const r = await rulesApi.cleanup();
              setUsage(r);
              setDone(`清掉 ${r.removed ?? 0} 個過期檔案${r.vacuumed ? "，已壓縮資料庫" : ""}`);
            } catch (e) {
              onError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "清理中…" : "🧹 立即清理"}
        </button>
        {done && <span className="muted">{done}</span>}
      </div>
    </div>
  );
}
