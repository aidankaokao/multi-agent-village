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

type Tab = "chat" | "villagers" | "traces" | "rules" | "llm";

const TABS: [Tab, string][] = [
  ["chat", "問答"],
  ["villagers", "村民"],
  ["traces", "紀錄"],
  ["rules", "村規"],
  ["llm", "LLM 設定"],
];

export function App() {
  const villageRef = useRef<VillageHandle>(null);
  const [tab, setTab] = useState<Tab>("chat");
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [runs, dispatch] = useReducer(runsReducer, initialRuns);
  const [traceId, setTraceId] = useState<string | null>(null); // 從問答跳到某筆紀錄
  const [traceTick, setTraceTick] = useState(0); // 問答結束 → 紀錄列表重新整理

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
    if (["run.finished", "run.failed", "run.cancelled"].includes(e.type)) setTraceTick((n) => n + 1);
  });

  const villagers = useMemo<VillagerInfo[]>(
    () => agents.map((a) => ({ id: a.id, name: a.name, sprite: a.sprite, home: a.home })),
    [agents],
  );

  const clearTraceId = useCallback(() => setTraceId(null), []);

  const send = async (message: string) => {
    const { run_id } = await village.chat(message);
    dispatch({ type: "local", run_id, message });
  };

  return (
    <div className={`layout ${tab === "villagers" || tab === "traces" ? "wide" : ""}`}>
      <main>
        <header>
          <h1>Multi-Agent Village</h1>
        </header>
        <VillageCanvas ref={villageRef} villagers={villagers} />
      </main>
      <aside>
        <nav className="tabs">
          {TABS.map(([k, label]) => (
            <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>
              {label}
            </button>
          ))}
        </nav>
        {/* 問答分頁切走時保留狀態，所以用 display 切換而不是卸載 */}
        <div className="tab-pane" style={{ display: tab === "chat" ? "flex" : "none" }}>
          <ChatPanel
            runs={runs}
            agents={agents}
            onSend={send}
            onReset={() => dispatch({ type: "reset" })}
            onCancel={async (id) => void (await village.cancel(id))}
            onOpenTrace={(id) => {
              setTraceId(id);
              setTab("traces");
            }}
          />
        </div>
        {tab === "villagers" && <VillagerManager />}
        {tab === "traces" && <TracePanel agents={agents} openId={traceId} onOpened={clearTraceId} refreshKey={traceTick} />}
        {tab === "rules" && <RulesPanel />}
        {tab === "llm" && <LlmSettings />}
      </aside>
    </div>
  );
}
