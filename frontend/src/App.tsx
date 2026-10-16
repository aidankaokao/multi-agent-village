import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { village, type AgentInfo } from "./api";
import { ChatPanel } from "./chat/ChatPanel";
import { initialRuns, runsReducer } from "./chat/runs";
import { useVillageEvents } from "./events";
import { RulesPanel } from "./rules/RulesPanel";
import { LlmSettings } from "./settings/LlmSettings";
import { TracePanel } from "./traces/TracePanel";
import { VillagerManager } from "./villagers/VillagerManager";
import { VillageCanvas, type VillageHandle } from "./village/VillageCanvas";
import type { VillagerInfo } from "./village/VillageScene";

type Page = "villagers" | "traces" | "rules" | "llm";

const PAGES: { id: Page; icon: string; label: string }[] = [
  { id: "villagers", icon: "🧑‍🌾", label: "村民" },
  { id: "traces", icon: "📜", label: "紀錄" },
  { id: "rules", icon: "⚖️", label: "村規" },
  { id: "llm", icon: "🧠", label: "LLM 設定" },
];

/**
 * 版面：像素地圖滿版；右上 ⚙️ 展開頁面選單，頁面從右側抽屜滑出（左邊仍看得到村子）；
 * 右下 💬 打開問答視窗。抽屜與問答視窗同時只開一個（進行中的問答不受影響）。
 */
export function App() {
  const villageRef = useRef<VillageHandle>(null);
  const [page, setPage] = useState<Page | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [unseen, setUnseen] = useState(false); // 問答視窗收著時有問答完成 → 💬 上的紅點
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [runs, dispatch] = useReducer(runsReducer, initialRuns);
  const [traceId, setTraceId] = useState<string | null>(null); // 從問答跳到某筆紀錄
  const [traceTick, setTraceTick] = useState(0); // 問答結束 → 紀錄列表重新整理
  const closeTimer = useRef<number | undefined>(undefined);
  const chatOpenRef = useRef(chatOpen);
  chatOpenRef.current = chatOpen;

  const loadAgents = useCallback(() => {
    village
      .agents()
      .then((r) => setAgents(r.agents))
      .catch(() => {});
  }, []);

  useEffect(loadAgents, [loadAgents]);

  useVillageEvents((e) => {
    villageRef.current?.dispatch(e);
    if (e.type === "agents.changed") loadAgents();
    else dispatch({ type: "event", event: e });
    if (["run.finished", "run.failed", "run.cancelled"].includes(e.type)) {
      setTraceTick((n) => n + 1);
      if (!chatOpenRef.current && e.run_id && runs.byId[e.run_id]) setUnseen(true);
    }
  });

  const villagers = useMemo<VillagerInfo[]>(
    () => agents.map((a) => ({ id: a.id, name: a.name, sprite: a.sprite, home: a.home })),
    [agents],
  );
  const busy = runs.order.some((id) => ["planning", "running"].includes(runs.byId[id].status));

  const openPage = (p: Page | null) => {
    setPage(p);
    setMenuOpen(false);
    if (p) setChatOpen(false);
  };
  const toggleChat = () => {
    const next = !chatOpen;
    setChatOpen(next);
    if (next) {
      setPage(null);
      setMenuOpen(false);
      setUnseen(false);
    }
  };

  // Esc：先關選單，再關抽屜 / 問答視窗；抽屜裡還有彈窗時交給彈窗處理
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || document.querySelector(".modal-backdrop")) return;
      if (menuOpen) setMenuOpen(false);
      else if (page) setPage(null);
      else if (chatOpen) setChatOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen, page, chatOpen]);

  const clearTraceId = useCallback(() => setTraceId(null), []);

  const send = async (message: string) => {
    const { run_id } = await village.chat(message);
    dispatch({ type: "local", run_id, message });
  };

  const current = PAGES.find((p) => p.id === page);

  return (
    <div className={`stage ${page ? "with-drawer" : ""}`}>
      <div className="map" onMouseDown={() => setMenuOpen(false)}>
        <VillageCanvas ref={villageRef} villagers={villagers} />
      </div>
      <div className="village-name">Multi-Agent Village</div>

      {/* 右上：頁面選單（抽屜打開時改用抽屜上方的切換列） */}
      {!page && (
        <nav
          className={`fab-menu ${menuOpen ? "open" : ""}`}
          // 滑鼠移過去就展開；移開後稍等再收，移到選項的途中不會先收掉（觸控螢幕仍可用點的）
          onPointerEnter={(e) => {
            if (e.pointerType !== "mouse") return; // 觸控點一下也會觸發 enter，交給 onClick
            window.clearTimeout(closeTimer.current);
            setMenuOpen(true);
          }}
          onPointerLeave={(e) => {
            if (e.pointerType !== "mouse") return;
            closeTimer.current = window.setTimeout(() => setMenuOpen(false), 300);
          }}
        >
          <button className="fab" onClick={() => setMenuOpen(!menuOpen)} title="選單" aria-expanded={menuOpen}>
            {menuOpen ? "✕" : "⚙️"}
          </button>
          {menuOpen &&
            PAGES.map((p, i) => (
              <button key={p.id} className="fab-item" style={{ animationDelay: `${i * 40}ms` }} onClick={() => openPage(p.id)}>
                <span className="fab-label">{p.label}</span>
                <span className="fab-icon">{p.icon}</span>
              </button>
            ))}
        </nav>
      )}

      {/* 右側抽屜：各設定頁 */}
      {current && (
        <aside className="drawer" key="drawer">
          <div className="drawer-head">
            <nav className="drawer-tabs">
              {PAGES.map((p) => (
                <button key={p.id} className={p.id === page ? "active" : ""} onClick={() => openPage(p.id)} title={p.label}>
                  <span>{p.icon}</span> {p.label}
                </button>
              ))}
            </nav>
            <button className="link drawer-close" onClick={() => openPage(null)} title="關閉（Esc）">
              ✕
            </button>
          </div>
          <div className="drawer-body">
            {page === "villagers" && <VillagerManager />}
            {page === "traces" && <TracePanel agents={agents} openId={traceId} onOpened={clearTraceId} refreshKey={traceTick} />}
            {page === "rules" && <RulesPanel />}
            {page === "llm" && <LlmSettings />}
          </div>
        </aside>
      )}

      {/* 右下：問答。收起時用 display 隱藏而不卸載，進行中的問答與輸入的文字都保留 */}
      <div className="chat-window" style={{ display: chatOpen ? "flex" : "none" }}>
        <div className="chat-window-head">
          <strong>💬 問答</strong>
          <span style={{ flex: 1 }} />
          <button className="link" onClick={toggleChat} title="收起（Esc）">
            ▾
          </button>
        </div>
        <ChatPanel
          runs={runs}
          agents={agents}
          onSend={send}
          onReset={() => dispatch({ type: "reset" })}
          onCancel={async (id) => void (await village.cancel(id))}
          onOpenTrace={(id) => {
            setTraceId(id);
            openPage("traces");
          }}
        />
      </div>
      <button className={`fab chat-fab ${busy ? "busy" : ""}`} onClick={toggleChat} title={chatOpen ? "收起問答" : "打開問答"}>
        {chatOpen ? "▾" : "💬"}
        {unseen && !chatOpen && <span className="fab-dot" />}
      </button>
    </div>
  );
}
