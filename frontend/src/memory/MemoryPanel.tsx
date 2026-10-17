import { useCallback, useEffect, useState } from "react";
import { memories, type MemoryRow } from "../api";

const KIND: Record<MemoryRow["kind"], string> = { fact: "事實", preference: "偏好" };

/** 長期記憶：書記記下的、或自己新增的「關於我」的事；只有自己看得到、只用在自己的問答 */
export function MemoryPanel() {
  const [list, setList] = useState<MemoryRow[]>([]);
  const [content, setContent] = useState("");
  const [kind, setKind] = useState<MemoryRow["kind"]>("fact");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");

  const reload = useCallback(() => memories.list().then(setList).catch((e) => setError(e.message)), []);
  useEffect(() => {
    reload();
  }, [reload]);

  const run = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      setError("");
      await reload();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  };

  return (
    <div className="panel-body">
      <p className="hint">
        書記會在問答後記下關於你本人、長期有效的事（名字、偏好、生日…），之後的問答村民就會參考，換了對話也記得。只有你看得到、只用在你自己的問答。
        想讓村子忘記某件事，可以在這裡刪除，或直接跟村子說「忘記我的…」。
      </p>
      {error && (
        <p className="error" onClick={() => setError("")}>
          {error}
        </p>
      )}
      <form
        className="card"
        onSubmit={async (e) => {
          e.preventDefault();
          if (await run(() => memories.create(content.trim(), kind))) setContent("");
        }}
      >
        <div className="row">
          <input value={content} onChange={(e) => setContent(e.target.value)} placeholder="手動新增，例如：使用者希望回答用條列" style={{ flex: 1 }} maxLength={300} />
          <select value={kind} onChange={(e) => setKind(e.target.value as MemoryRow["kind"])}>
            <option value="fact">事實</option>
            <option value="preference">偏好</option>
          </select>
          <button type="submit" disabled={!content.trim()}>
            ＋ 記住
          </button>
        </div>
      </form>

      {list.length === 0 && <p className="muted">還沒有記憶。試著在問答裡說「我叫…」「以後回答簡短一點」。</p>}
      {list.map((m) => (
        <div key={m.id} className={`card list-row ${m.enabled ? "" : "disabled"}`}>
          <div className="list-main">
            {editing === m.id ? (
              <form
                className="row"
                onSubmit={async (e) => {
                  e.preventDefault();
                  if (await run(() => memories.update(m.id, { content: draft.trim() }))) setEditing(null);
                }}
              >
                <input value={draft} onChange={(e) => setDraft(e.target.value)} style={{ flex: 1 }} autoFocus maxLength={300} />
                <button type="submit">儲存</button>
                <button type="button" className="link" onClick={() => setEditing(null)}>
                  取消
                </button>
              </form>
            ) : (
              <div>{m.content}</div>
            )}
            <div className="row muted">
              <span className={`tag ${m.kind === "preference" ? "ok" : ""}`}>{KIND[m.kind]}</span>
              <span>{m.source === "auto" ? "📝 書記記下" : "✍️ 手動新增"}</span>
              <span>・用過 {m.use_count} 次</span>
            </div>
          </div>
          <label className="toggle" title="停用後問答不會帶入，但不會刪除">
            <input type="checkbox" checked={m.enabled} onChange={(e) => run(() => memories.update(m.id, { enabled: e.target.checked }))} />
            使用
          </label>
          <button
            className="link"
            onClick={() => {
              setEditing(m.id);
              setDraft(m.content);
            }}
          >
            修改
          </button>
          <button className="link danger" onClick={() => run(() => memories.remove(m.id))}>
            刪除
          </button>
        </div>
      ))}
      {list.length > 0 && (
        <button className="link danger" onClick={() => confirm("清除所有記憶？書記會忘掉關於你的一切。") && run(() => memories.clear())}>
          全部清除
        </button>
      )}
    </div>
  );
}
