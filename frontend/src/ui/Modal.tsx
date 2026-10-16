import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** 彈窗：點背景或按 Esc 關閉；打開時有淡入 + 彈出動畫（styles.css 的 modal-in） */
export function Modal({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean; // 放大看圖用
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 掛到 body：放在問答視窗或抽屜裡打開時，也不會被它們的層級或大小限制住
  return createPortal(
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true">
        <div className="modal-head">
          <strong>{title}</strong>
          <span style={{ flex: 1 }} />
          <button className="link" onClick={onClose} aria-label="關閉">
            ✕
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
