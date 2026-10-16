import type { Attachment } from "../api";

/** 工具產生的圖片：用 <img> 顯示 SVG（瀏覽器不會執行 img 裡的程式碼），可以下載 */
export function Attachments({ items }: { items?: Attachment[] }) {
  if (!items?.length) return null;
  return (
    <div className="attachments">
      {items.map((a, i) => {
        const url = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(a.data)}`;
        return (
          <figure key={i}>
            <img src={url} alt={a.title || a.name} />
            <figcaption className="row">
              <span className="muted">{a.title}</span>
              <span style={{ flex: 1 }} />
              <a className="link" href={url} download={a.name}>
                ⬇ 下載
              </a>
            </figcaption>
          </figure>
        );
      })}
    </div>
  );
}
