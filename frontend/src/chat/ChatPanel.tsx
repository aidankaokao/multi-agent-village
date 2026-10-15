import type { VillageEvent } from "../events";

/** Phase 0：還沒有真正的問答，先提供示範按鈕與事件紀錄，驗證村落動畫。 */
export function ChatPanel({ log }: { log: VillageEvent[] }) {
  return (
    <div className="panel-body">
      <p className="hint">問答功能將在 Phase 1（詩人 × 笑話師）啟用。現在可以先播放一段示範事件。</p>
      <button onClick={() => fetch("/api/debug/demo-run", { method: "POST" })}>▶ 播放示範：村長 → 詩人 → 笑話師</button>
      <h3>事件紀錄</h3>
      <ul className="event-log">
        {log.length === 0 && <li className="muted">尚無事件</li>}
        {log.map((e, i) => (
          <li key={i}>
            <span className="muted">{e.ts.slice(11, 19)}</span> <code>{e.type}</code>{" "}
            {e.agent ?? (e.from_agent ? `${e.from_agent} → ${e.to_agent}` : "")}
          </li>
        ))}
      </ul>
    </div>
  );
}
