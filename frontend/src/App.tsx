import { useRef, useState } from "react";
import { ChatPanel } from "./chat/ChatPanel";
import { useVillageEvents, type VillageEvent } from "./events";
import { LlmSettings } from "./settings/LlmSettings";
import { VillageCanvas, type VillageHandle } from "./village/VillageCanvas";

type Tab = "chat" | "llm";

export function App() {
  const village = useRef<VillageHandle>(null);
  const [tab, setTab] = useState<Tab>("chat");
  const [log, setLog] = useState<VillageEvent[]>([]);

  useVillageEvents((e) => {
    village.current?.dispatch(e);
    setLog((l) => [e, ...l].slice(0, 100));
  });

  return (
    <div className="layout">
      <main>
        <header>
          <h1>Multi-Agent Village</h1>
        </header>
        <VillageCanvas ref={village} />
      </main>
      <aside>
        <nav className="tabs">
          <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>
            問答
          </button>
          <button className={tab === "llm" ? "active" : ""} onClick={() => setTab("llm")}>
            LLM 設定
          </button>
        </nav>
        {tab === "chat" ? <ChatPanel log={log} /> : <LlmSettings />}
      </aside>
    </div>
  );
}
