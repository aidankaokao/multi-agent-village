import { useState } from "react";
import type { Attachment } from "../api";
import { Modal } from "./Modal";

/** 工具產生的圖片：SVG 用 <img> 顯示（瀏覽器不會執行 img 裡的程式碼）；畫家的畫作用網址讀取。點圖放大、可以下載 */
export function Attachments({ items }: { items?: Attachment[] }) {
  const [zoom, setZoom] = useState<number | null>(null);
  const [expired, setExpired] = useState<Set<number>>(new Set());
  if (!items?.length) return null;
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
