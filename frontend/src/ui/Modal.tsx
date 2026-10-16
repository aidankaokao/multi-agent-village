import { useEffect, type ReactNode } from "react";

/** 彈窗：點背景或按 Esc 關閉；打開時有淡入 + 彈出動畫（styles.css 的 modal-in） */
export function Modal({ title, onClose, children }: { title: ReactNode; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true">
        <div className="modal-head">
          <strong>{title}</strong>
          <span style={{ flex: 1 }} />
          <button className="link" onClick={onClose} aria-label="關閉">
            ✕
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
