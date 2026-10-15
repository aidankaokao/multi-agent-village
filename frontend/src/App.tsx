import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { village, type AgentInfo } from "./api";
import { ChatPanel } from "./chat/ChatPanel";
import { initialRuns, runsReducer } from "./chat/runs";
import { useVillageEvents } from "./events";
import { LlmSettings } from "./settings/LlmSettings";
import { VillageCanvas, type VillageHandle } from "./village/VillageCanvas";
import type { VillagerInfo } from "./village/VillageScene";

type Tab = "chat" | "llm";

export function App() {
  const villageRef = useRef<VillageHandle>(null);
  const [tab, setTab] = useState<Tab>("chat");
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [runs, dispatch] = useReducer(runsReducer, initialRuns);

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
  });

  const villagers = useMemo<VillagerInfo[]>(
    () => agents.map((a) => ({ id: a.id, name: a.name, sprite: a.sprite, home: a.home })),
    [agents],
  );

  const send = async (message: string) => {
    const { run_id } = await village.chat(message);
    dispatch({ type: "local", run_id, message });
  };

  return (
    <div className="layout">
      <main>
        <header>
          <h1>Multi-Agent Village</h1>
        </header>
        <VillageCanvas ref={villageRef} villagers={villagers} />
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
        {/* 問答分頁切走時保留狀態，所以用 display 切換而不是卸載 */}
        <div className="tab-pane" style={{ display: tab === "chat" ? "flex" : "none" }}>
          <ChatPanel runs={runs} agents={agents} onSend={send} />
        </div>
        {tab === "llm" && <LlmSettings />}
      </aside>
    </div>
  );
}
