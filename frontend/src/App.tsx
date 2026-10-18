import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { AccountsPanel, DeleteAccountModal, PasswordModal } from "./accounts/AccountsPanel";
import { auth, conversations, UNAUTHORIZED, village, type AgentInfo, type AuthStatus, type ConversationRow, type Me } from "./api";
import { LoginPage } from "./auth/LoginPage";
import { MemoryPanel } from "./memory/MemoryPanel";
import { ChatPanel, type Vision } from "./chat/ChatPanel";
import type { DocFile } from "./chat/docs";
import { initialRuns, runsReducer } from "./chat/runs";
import { useVillageEvents } from "./events";
import { RulesPanel } from "./rules/RulesPanel";
import { LlmSettings } from "./settings/LlmSettings";
import { TracePanel } from "./traces/TracePanel";
import { VillagerManager } from "./villagers/VillagerManager";
import { VillageCanvas, type VillageHandle } from "./village/VillageCanvas";
import type { VillagerInfo } from "./village/VillageScene";

type Page = "villagers" | "traces" | "memories" | "rules" | "accounts" | "llm";

const PAGES: { id: Page; icon: string; label: string; admin?: boolean }[] = [
  { id: "villagers", icon: "🧑‍🌾", label: "村民", admin: true },
  { id: "traces", icon: "📜", label: "紀錄" },
  { id: "memories", icon: "📝", label: "記憶" },
  { id: "rules", icon: "⚖️", label: "村規", admin: true },
  { id: "accounts", icon: "👥", label: "帳號", admin: true },
  { id: "llm", icon: "🧠", label: "LLM 設定" }, // 每個人管理自己的 LLM（自己的 API key 與額度）
];

/** 最外層：還沒登入就顯示登入頁（示範村落 + 登入 / 註冊），登入後進入村子 */
export function App() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const load = useCallback(() => {
    auth
      .status()
      .then(setStatus)
      .catch(() => setStatus({ setup_required: false, allow_registration: false, user: null, llm_ready: false, vision_ready: false, max_images: 0 }));
  }, []);

  useEffect(() => {
    load();
    window.addEventListener(UNAUTHORIZED, load); // session 過期或被管理員停用 → 回登入頁
    return () => window.removeEventListener(UNAUTHORIZED, load);
  }, [load]);

  if (!status) return null;
  if (!status.user) return <LoginPage status={status} onDone={() => load()} />;
  return (
    <VillageApp
      key={status.user.id}
      me={status.user}
      llmReady={status.llm_ready}
      vision={{ ready: status.vision_ready, maxImages: status.max_images }}
      onRecheck={load}
      onLogout={async () => {
        await auth.logout().catch(() => {});
        load();
      }}
    />
  );
}

/**
 * 版面：像素地圖滿版；右上 ⚙️ 展開頁面選單，頁面從右側抽屜滑出（左邊仍看得到村子）；
 * 右下 💬 打開問答視窗。抽屜與問答視窗同時只開一個（進行中的問答不受影響）。
 */
/** 系統角色被點到時的自我介紹（他們不接受 @ 指名） */
const SYSTEM_SAY: Record<string, string> = {
  craftsman: "到「村民」頁找我打造新村民",
  reviewer: "答案交出前我會檢查",
  scribe: "我會記住你說過的事",
  appraiser: "附圖給我，我幫大家看",
};

/** 從名片產生閒聊短句與一句話專長（泡泡不大，控制在十來個字） */
function idleLines(a: AgentInfo): { lines: string[]; skill: string } {
  // 說明與技能各取第一句，挑 10 個字以內、比較短的；都太長就不講專長（截斷的半句話反而不通順）
  const clause = (t?: string) => (t || "").split(/[，。、；：,.;:（(\n]/)[0].trim().replace(/^(會|能|可以|負責|幫你|幫忙|專門)/, "");
  const skill = [clause(a.description), clause(a.skills[0]?.description)]
    .filter((c) => c && c.length <= 10)
    .sort((x, y) => x.length - y.length)[0] ?? "";
  const lines = [`我是${a.name}！`, "點我就能問我"];
  if (skill) lines.unshift(`我會${skill}`);
  if (a.kind === "remote") lines.push("我從外地來幫忙～");
  return { lines: lines.map((l) => (l.length > 14 ? l.slice(0, 13) + "…" : l)), skill };
}

function VillageApp({
  me,
  llmReady,
  vision,
  onRecheck,
  onLogout,
}: {
  me: Me;
  llmReady: boolean;
  vision: Vision;
  onRecheck: () => void;
  onLogout: () => void;
}) {
  const [deleting, setDeleting] = useState(false);
  const pages = PAGES.filter((p) => !p.admin || me.role === "admin");
  const [userMenu, setUserMenu] = useState(false);
  const [changingPassword, setChangingPassword] = useState(false);
  const villageRef = useRef<VillageHandle>(null);
  const [page, setPage] = useState<Page | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  // 問答的兩種樣子：右下小視窗，或畫面中央接近滿版的大視窗（記住使用者上次用哪種）
  const [chatBig, setChatBigState] = useState(() => {
    try {
      return localStorage.getItem("chat-size") === "big";
    } catch {
      return false;
    }
  });
  const setChatBig = (big: boolean) => {
    setChatBigState(big);
    try {
      localStorage.setItem("chat-size", big ? "big" : "small");
    } catch {
      /* 無痕模式等存不了：只是下次不記得 */
    }
  };
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
    if (e.type === "agents.changed" || e.type === "residents.status") loadAgents();
    else dispatch({ type: "event", event: e });
    if (["run.finished", "run.failed", "run.cancelled"].includes(e.type)) {
      setTraceTick((n) => n + 1);
      if (!chatOpenRef.current && e.run_id && runs.byId[e.run_id]) setUnseen(true);
    }
  });

  const villagers = useMemo<VillagerInfo[]>(
    () =>
      agents.map((a) => ({
        id: a.id,
        name: a.name,
        sprite: a.sprite,
        home: a.home,
        away: a.kind === "remote" && a.status !== "online",
        ...idleLines(a),
      })),
    [agents],
  );
  const busy = runs.order.some((id) => ["planning", "running"].includes(runs.byId[id].status));

  const openPage = (p: Page | null) => {
    if ((page === "llm" || page === "rules") && p !== page) onRecheck(); // 離開 LLM 設定 / 村規時重新確認（LLM、視覺模型、附圖張數）
    setPage(p);
    setMenuOpen(false);
    if (p) setChatOpen(false);
  };
  const toggleChat = () => {
    const next = !chatOpen;
    setChatOpen(next);
    if (next) {
      openPage(null);
      setUnseen(false);
    }
  };

  // 帳號選單：點其他地方就收起
  useEffect(() => {
    if (!userMenu) return;
    const close = (e: MouseEvent) => !(e.target as Element).closest(".user-chip") && setUserMenu(false);
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [userMenu]);

  // Esc：先關選單，再關抽屜 / 問答視窗；抽屜裡還有彈窗時交給彈窗處理
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || document.querySelector(".modal-backdrop")) return;
      if (menuOpen) setMenuOpen(false);
      else if (page) openPage(null);
      else if (chatOpen && chatBig) setChatBig(false); // 大視窗先縮回右下
      else if (chatOpen) setChatOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen, page, chatOpen, chatBig]);

  const clearTraceId = useCallback(() => setTraceId(null), []);

  // ---------------------------------------------------------------- 點地圖上的村民
  const [prefill, setPrefill] = useState<{ text: string; nonce: number } | null>(null);
  const clickVillager = (id: string) => {
    const say = (text: string) => villageRef.current?.say(id, text);
    if (SYSTEM_SAY[id]) return say(SYSTEM_SAY[id]); // 工匠、審核員、書記、鑑定師不能被指名派工：自我介紹
    const a = agents.find((x) => x.id === id);
    if (a?.kind === "remote" && a.status !== "online") return say("我外出中，晚點再來");
    say(id === "mayor" ? "有什麼問題呢？" : "你好！請說～");
    openConversation(null); // 開新對話
    openPage(null);
    setChatOpen(true);
    setUnseen(false);
    setPrefill({ text: id === "mayor" ? "" : `@${id} `, nonce: Date.now() });
  };

  // ---------------------------------------------------------------- 對話
  const [convs, setConvs] = useState<ConversationRow[]>([]);
  const [convId, setConvId] = useState<string | null>(null);
  const loadConvs = useCallback(() => conversations.list().then(setConvs).catch(() => []), []);

  const openConversation = useCallback(async (id: string | null) => {
    setConvId(id);
    if (!id) return dispatch({ type: "reset" });
    try {
      dispatch({ type: "load", messages: await conversations.messages(id) });
    } catch {
      dispatch({ type: "reset" });
    }
  }, []);

  // 進村子時接回最近的對話（重新整理也不會不見）
  useEffect(() => {
    conversations
      .list()
      .then((list) => {
        setConvs(list);
        if (list[0]) openConversation(list[0].id);
      })
      .catch(() => {});
  }, [openConversation]);

  const send = async (message: string, images: string[], docs: DocFile[], audio: { id: string; name: string; size: number }[]) => {
    const { run_id, conversation_id } = await village.chat(message, convId, images, docs, audio.map((a) => a.id));
    if (conversation_id !== convId) setConvId(conversation_id);
    const fallback = audio.length ? "請把這段錄音轉成逐字稿" : docs.length ? "請彙整這些文件的重點" : "這張圖片裡有什麼？";
    const files = [...docs.map(({ name, size }) => ({ name, size })), ...audio.map(({ name, size }) => ({ name, size, audio: true }))];
    dispatch({ type: "local", run_id, message: message || fallback, images, files });
    loadConvs();
  };

  const current = pages.find((p) => p.id === page);

  return (
    <div className={`stage ${page ? "with-drawer" : ""}`}>
      <div className="map" onMouseDown={() => setMenuOpen(false)}>
        <VillageCanvas ref={villageRef} villagers={villagers} onVillagerClick={clickVillager} />
      </div>
      <div className="village-name">Multi-Agent Village</div>
      <div className="user-chip">
        <button onClick={() => setUserMenu(!userMenu)} title="帳號">
          👤 {me.display_name}
          {me.role === "admin" && <span className="muted">（管理員）</span>} ▾
        </button>
        {userMenu && (
          <div className="user-menu">
            <span className="muted">@{me.username}</span>
            <button onClick={() => (openPage("llm"), setUserMenu(false))}>🧠 我的 LLM 設定</button>
            <button onClick={() => (setChangingPassword(true), setUserMenu(false))}>🔑 修改密碼</button>
            <button onClick={onLogout}>🚪 登出</button>
            <button className="danger-text" onClick={() => (setDeleting(true), setUserMenu(false))}>
              🗑 刪除我的帳號
            </button>
          </div>
        )}
      </div>
      {changingPassword && <PasswordModal onClose={() => setChangingPassword(false)} />}
      {deleting && <DeleteAccountModal me={me} onClose={() => setDeleting(false)} onDeleted={onLogout} />}
      {!llmReady && page !== "llm" && (
        <div className="llm-banner">
          ⚠️ 你還沒有設定 LLM。每個帳號用自己的模型與額度，設定好才能問答。
          <button onClick={() => openPage("llm")}>去設定 →</button>
        </div>
      )}

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
            pages.map((p, i) => (
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
              {pages.map((p) => (
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
            {page === "memories" && <MemoryPanel />}
            {page === "rules" && <RulesPanel />}
            {page === "accounts" && <AccountsPanel me={me} />}
            {page === "llm" && <LlmSettings />}
          </div>
        </aside>
      )}

      {/* 右下：問答。收起時用 display 隱藏而不卸載，進行中的問答與輸入的文字都保留 */}
      {chatOpen && chatBig && <div className="chat-backdrop" onMouseDown={() => setChatBig(false)} />}
      <div className={`chat-window ${chatBig ? "big" : ""}`} style={{ display: chatOpen ? "flex" : "none" }}>
        <div className="chat-window-head">
          <strong>💬 問答</strong>
          <span style={{ flex: 1 }} />
          {chatBig ? (
            <button className="link chat-size" onClick={() => setChatBig(false)} title="關閉大視窗，回到右下（Esc）">
              ✕
            </button>
          ) : (
            <>
              <button className="link chat-size" onClick={() => setChatBig(true)} title="放大到畫面中央（適合連續問答）">
                ⛶
              </button>
              <button className="link" onClick={toggleChat} title="收起（Esc）">
                ▾
              </button>
            </>
          )}
        </div>
        <ChatPanel
          runs={runs}
          agents={agents}
          onSend={send}
          vision={vision}
          prefill={prefill}
          onReset={() => openConversation(null)}
          conversations={convs}
          currentConversation={convId}
          onOpenConversation={openConversation}
          onRenameConversation={async (id, title) => {
            await conversations.rename(id, title);
            loadConvs();
          }}
          onDeleteConversation={async (id) => {
            await conversations.remove(id);
            if (id === convId) openConversation(null);
            loadConvs();
          }}
          onCancel={async (id) => void (await village.cancel(id))}
          onOpenTrace={(id) => {
            setTraceId(id);
            openPage("traces");
          }}
          onOpenLlm={() => openPage("llm")}
        />
      </div>
      <button className={`fab chat-fab ${busy ? "busy" : ""}`} onClick={toggleChat} title={chatOpen ? "收起問答" : "打開問答"}>
        {chatOpen ? "▾" : "💬"}
        {unseen && !chatOpen && <span className="fab-dot" />}
      </button>
    </div>
  );
}
