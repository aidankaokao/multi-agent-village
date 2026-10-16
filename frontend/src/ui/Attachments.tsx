import { useState } from "react";
import type { Attachment } from "../api";
import { Modal } from "./Modal";

/** 工具產生的圖片：用 <img> 顯示 SVG（瀏覽器不會執行 img 裡的程式碼）；點圖放大、可以下載 */
export function Attachments({ items }: { items?: Attachment[] }) {
  const [zoom, setZoom] = useState<number | null>(null);
  if (!items?.length) return null;
  const url = (a: Attachment) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(a.data)}`;
  const big = zoom !== null ? items[zoom] : null;
  return (
    <div className="attachments">
      {items.map((a, i) => (
        <figure key={i}>
          <img src={url(a)} alt={a.title || a.name} title="點一下放大" onClick={() => setZoom(i)} />
          <figcaption className="row">
            <span className="muted">{a.title}</span>
            <span style={{ flex: 1 }} />
            <button className="link" onClick={() => setZoom(i)}>
              🔍 放大
            </button>
            <a className="link" href={url(a)} download={a.name}>
              ⬇ 下載
            </a>
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
