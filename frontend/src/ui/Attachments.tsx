import { useState } from "react";
import type { Attachment } from "../api";
import { download, fmtSize, type DocChip } from "../chat/docs";
import { Modal } from "./Modal";

/** 工具 / 駐村專家產生的附件：檔案（報告 PDF）顯示成下載卡片，圖片另外排 */
export function Attachments({ items }: { items?: Attachment[] }) {
  if (!items?.length) return null;
  const files = items.filter((a) => a.kind === "file");
  const pictures = items.filter((a) => a.kind !== "file");
  return (
    <>
      {files.length > 0 && <FileCards items={files} />}
      {pictures.length > 0 && <Pictures items={pictures} />}
    </>
  );
}

/** 駐村專家回傳的檔案（例如文件彙整師的報告 PDF）：點一下下載；過期（14 天）就顯示已過期 */
function FileCards({ items }: { items: Attachment[] }) {
  const [state, setState] = useState<Record<number, string>>({});
  return (
    <div className="file-cards">
      {items.map((a, i) => (
        <div key={i} className="file-card">
          <span className="file-icon">📄</span>
          <span className="clamp">{a.name}</span>
          <span style={{ flex: 1 }} />
          {state[i] && state[i] !== "…" ? (
            <span className="muted">{state[i]}</span>
          ) : (
            <button
              type="button"
              disabled={state[i] === "…"}
              onClick={() => {
                setState((s) => ({ ...s, [i]: "…" }));
                download(a.url ?? "", a.name)
                  .then(() => setState((s) => ({ ...s, [i]: "" })))
                  .catch((e) => setState((s) => ({ ...s, [i]: (e as Error).message })));
              }}
            >
              {state[i] === "…" ? "下載中…" : "⬇ 下載"}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

/** 使用者附的文件：小標籤；有網址（從紀錄載入）就可以點來下載原檔 */
export function DocChips({ files, onRemove }: { files?: DocChip[]; onRemove?: (i: number) => void }) {
  const [error, setError] = useState("");
  if (!files?.length) return null;
  return (
    <div className="doc-chips">
      {files.map((f, i) => (
        <span key={i} className="doc-chip" title={f.url ? "點一下下載原檔" : f.name}>
          {f.url ? (
            <button type="button" className="link" onClick={() => download(f.url!, f.name).catch((e) => setError((e as Error).message))}>
              📄 {f.name}
            </button>
          ) : (
            <span>📄 {f.name}</span>
          )}
          {f.size != null && <span className="muted">{fmtSize(f.size)}</span>}
          {onRemove && (
            <button type="button" className="link" title="移除" onClick={() => onRemove(i)}>
              ✕
            </button>
          )}
        </span>
      ))}
      {error && <span className="muted">{error}</span>}
    </div>
  );
}

/** 工具產生的圖片：SVG 用 <img> 顯示（瀏覽器不會執行 img 裡的程式碼）；畫家的畫作用網址讀取。點圖放大、可以下載 */
function Pictures({ items }: { items: Attachment[] }) {
  const [zoom, setZoom] = useState<number | null>(null);
  const [expired, setExpired] = useState<Set<number>>(new Set());
  const url = (a: Attachment) => (a.kind === "image" ? (a.url ?? "") : `data:image/svg+xml;charset=utf-8,${encodeURIComponent(a.data)}`);
  const gone = (i: number) => setExpired((s) => new Set(s).add(i));
  const big = zoom !== null ? items[zoom] : null;
  return (
    <div className="attachments">
      {items.map((a, i) => (
        <figure key={i}>
          {expired.has(i) ? (
            <div className="expired-image">🖼️ 圖片已過期（畫作保存 14 天）</div>
          ) : (
            <img src={url(a)} alt={a.title || a.name} title="點一下放大" onClick={() => setZoom(i)} onError={() => gone(i)} />
          )}
          <figcaption className="row">
            <span className="muted">{a.title}</span>
            <span style={{ flex: 1 }} />
            {!expired.has(i) && (
              <>
                <button className="link" onClick={() => setZoom(i)}>
                  🔍 放大
                </button>
                <a className="link" href={url(a)} download={a.name}>
                  ⬇ 下載
                </a>
              </>
            )}
          </figcaption>
        </figure>
      ))}
      {big && (
        <Modal title={big.title || big.name} onClose={() => setZoom(null)} wide>
          <img className="zoomed" src={url(big)} alt={big.title || big.name} />
          <div className="row">
            {items.length > 1 && (
              <>
                <button className="link" disabled={zoom === 0} onClick={() => setZoom((z) => (z ?? 0) - 1)}>
                  ← 上一張
                </button>
                <span className="muted">
                  {zoom! + 1} / {items.length}
                </span>
                <button className="link" disabled={zoom === items.length - 1} onClick={() => setZoom((z) => (z ?? 0) + 1)}>
                  下一張 →
                </button>
              </>
            )}
            <span style={{ flex: 1 }} />
            <a className="link" href={url(big)} download={big.name}>
              ⬇ 下載
            </a>
          </div>
        </Modal>
      )}
    </div>
  );
}

/** 使用者附的圖：小縮圖排一排，點一下放大 */
export function ImageStrip({ urls }: { urls: string[] }) {
  const [zoom, setZoom] = useState<number | null>(null);
  return (
    <div className="image-strip">
      {urls.map((u, i) => (
        <img key={u + i} src={u} alt={`附圖 ${i + 1}`} title="點一下放大" onClick={() => setZoom(i)} />
      ))}
      {zoom !== null && (
        <Modal title={`附圖 ${zoom + 1} / ${urls.length}`} onClose={() => setZoom(null)} wide>
          <img className="zoomed" src={urls[zoom]} alt={`附圖 ${zoom + 1}`} />
          {urls.length > 1 && (
            <div className="row">
              <button className="link" disabled={zoom === 0} onClick={() => setZoom(zoom - 1)}>
                ← 上一張
              </button>
              <span style={{ flex: 1 }} />
              <button className="link" disabled={zoom === urls.length - 1} onClick={() => setZoom(zoom + 1)}>
                下一張 →
              </button>
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
