/**
 * 島嶼版的介面層：膠囊工具列（時段、名牌、搬運、協作線、生活、暫停、鳥瞰、日誌）與「村莊日誌 LIVE」。
 * 開關記在 localStorage（island-prefs：島嶼版時代的名字，沿用才記得住舊設定）；網址 ?time= 會蓋過記住的時段。
 */
import { useEffect, useState } from "react";
import type { VillageEvent } from "../events";

export type TimeChoice = "auto" | "dawn" | "day" | "dusk" | "night";
export const TIME_MINUTES: Record<Exclude<TimeChoice, "auto">, number> = { dawn: 380, day: 600, dusk: 1100, night: 1320 };

export interface Prefs {
  time: TimeChoice;
  names: boolean;
  carry: boolean;
  flows: boolean;
  life: boolean;
  log: boolean;
}

const DEFAULT_PREFS: Prefs = { time: "auto", names: true, carry: true, flows: true, life: true, log: false };

export function loadPrefs(): Prefs {
  try {
    return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem("island-prefs") || "{}") };
  } catch {
    return DEFAULT_PREFS;
  }
}

export function savePrefs(p: Prefs) {
  try {
    localStorage.setItem("island-prefs", JSON.stringify(p));
  } catch {
    /* 存不了：只是下次不記得 */
  }
}

// ---------------------------------------------------------------- 日誌

export interface LogEntry {
  key: number;
  time: string;
  icon: string;
  text: string;
  agent?: string; // 點一下鏡頭飛過去
  kind: "info" | "ok" | "bad";
}

let seq = 0;

/** 事件 → 一句話（看不懂的事件不記） */
export function describe(e: VillageEvent, name: (id?: string) => string, carry: (id: string) => string): Omit<LogEntry, "key" | "time"> | null {
  const who = name(e.agent);
  switch (e.type) {
    case "run.started":
      return { icon: "🧭", text: "村長收到一個問題，開始規劃", agent: "mayor", kind: "info" };
    case "plan.created": {
      const steps = Array.isArray(e.steps) ? e.steps.length : 0;
      return { icon: "📋", text: steps ? `村長排好計畫：${steps} 個步驟` : "村長決定自己回答", agent: "mayor", kind: "info" };
    }
    case "appraise.started":
      return { icon: "🔍", text: "鑑定師開始看圖", agent: e.agent, kind: "info" };
    case "agent.started":
      return e.agent === "mayor" ? null : { icon: "⚙️", text: `${who}開始工作`, agent: e.agent, kind: "info" };
    case "agent.finished":
      return e.agent === "mayor" ? { icon: "💬", text: "村長回答了", agent: "mayor", kind: "ok" } : { icon: "✅", text: `${who}完成了`, agent: e.agent, kind: "ok" };
    case "agent.error":
      return { icon: "❗", text: `${who}出錯了`, agent: e.agent, kind: "bad" };
    case "agent.retry":
      return { icon: "↻", text: `${who}重做一次`, agent: e.agent, kind: "info" };
    case "agent.handoff":
      return { icon: carry(e.from_agent ?? ""), text: `${name(e.from_agent)}把成果交給${name(e.to_agent)}`, agent: e.to_agent, kind: "info" };
    case "loop.verdict":
      return {
        icon: "🔁",
        text: `${who}審查第 ${e.round ?? 1} 輪：${e.passed ? "通過" : "不通過，退回重做"}`,
        agent: e.agent,
        kind: e.passed ? "ok" : "bad",
      };
    case "review.started":
      return { icon: "🧐", text: "審核員檢查答案", agent: e.agent, kind: "info" };
    case "review.finished":
      return { icon: e.passed === false ? "↩️" : "👍", text: e.passed === false ? "審核員退件" : "審核員通過", agent: e.agent, kind: e.passed === false ? "bad" : "ok" };
    case "memory.started":
      return { icon: "📒", text: "書記在記筆記", agent: e.agent, kind: "info" };
    case "workshop.started":
      return { icon: "🔨", text: "工匠在打造新村民", agent: e.agent, kind: "info" };
    case "run.finished":
      return { icon: "🎉", text: "問答完成", kind: "ok" };
    case "run.failed":
      return { icon: "⚠️", text: "問答失敗", kind: "bad" };
    case "run.cancelled":
      return { icon: "⏹", text: "問答停止了", kind: "bad" };
  }
  return null;
}

export function entry(e: VillageEvent, name: (id?: string) => string, carry: (id: string) => string): LogEntry | null {
  const d = describe(e, name, carry);
  return d ? stamp(d) : null;
}

/** 加上時間與編號（場景自己寫的日誌，例如火車，也用這個） */
export function stamp(d: Omit<LogEntry, "key" | "time">): LogEntry {
  const t = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return { ...d, key: ++seq, time: `${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}` };
}

// ---------------------------------------------------------------- 工具列

interface HudProps {
  prefs: Prefs;
  setPrefs: (p: Prefs) => void;
  paused: boolean;
  setPaused: (p: boolean) => void;
  bird: boolean;
  setBird: (b: boolean) => void;
  canFollow: boolean; // 有火車的場景才顯示「跟車」
  follow: boolean;
  setFollow: (b: boolean) => void;
  log: LogEntry[];
  onFocus: (id: string) => void;
  onClearLog: () => void;
}

const TIMES: [TimeChoice, string, string][] = [
  ["auto", "🕒", "現在"],
  ["dawn", "🌅", "清晨"],
  ["day", "☀️", "白天"],
  ["dusk", "🌇", "黃昏"],
  ["night", "🌙", "夜晚"],
];

export function Hud({ prefs, setPrefs, paused, setPaused, bird, setBird, canFollow, follow, setFollow, log, onFocus, onClearLog }: HudProps) {
  const toggle = (k: "names" | "carry" | "flows" | "life" | "log") => setPrefs({ ...prefs, [k]: !prefs[k] });
  const [fresh, setFresh] = useState(false); // 日誌收著時有新事件 → 按鈕上的小點
  useEffect(() => {
    if (!prefs.log && log.length) setFresh(true);
  }, [log.length, prefs.log]);
  useEffect(() => {
    if (prefs.log) setFresh(false);
  }, [prefs.log]);

  return (
    <>
      <div className="isl-toolbar" role="toolbar" aria-label="島嶼顯示選項">
        <div className="isl-seg" role="group" aria-label="時段">
          {TIMES.map(([v, icon, label]) => (
            <button key={v} className={prefs.time === v ? "on" : ""} title={v === "auto" ? "跟著現在的時間" : label} onClick={() => setPrefs({ ...prefs, time: v })}>
              <span className="ico">{icon}</span>
              <span className="lbl">{label}</span>
            </button>
          ))}
        </div>
        <div className="isl-seg" role="group" aria-label="顯示">
          <button className={prefs.names ? "on" : ""} aria-pressed={prefs.names} onClick={() => toggle("names")} title="村民名牌">
            <span className="ico">🏷️</span>
            <span className="lbl">名牌</span>
          </button>
          <button className={prefs.carry ? "on" : ""} aria-pressed={prefs.carry} onClick={() => toggle("carry")} title="交接時頭上拿著的東西">
            <span className="ico">📦</span>
            <span className="lbl">搬運</span>
          </button>
          <button className={prefs.flows ? "on" : ""} aria-pressed={prefs.flows} onClick={() => toggle("flows")} title="交接的路線">
            <span className="ico">〰️</span>
            <span className="lbl">協作線</span>
          </button>
          <button className={prefs.life ? "on" : ""} aria-pressed={prefs.life} onClick={() => toggle("life")} title="生活作息：閒著的村民去釣魚、看書、打水，晚上回家睡覺（關掉就只是隨便走走）">
            <span className="ico">🏡</span>
            <span className="lbl">生活</span>
          </button>
          <button className={paused ? "on" : ""} aria-pressed={paused} onClick={() => setPaused(!paused)} title="暫停動畫（鏡頭照樣可以轉）">
            <span className="ico">{paused ? "▶️" : "⏸️"}</span>
            <span className="lbl">{paused ? "繼續" : "暫停"}</span>
          </button>
          <button className={bird ? "on" : ""} aria-pressed={bird} onClick={() => setBird(!bird)} title="從正上方看整個村子">
            <span className="ico">🦅</span>
            <span className="lbl">鳥瞰</span>
          </button>
          {canFollow && (
            <button className={follow ? "on" : ""} aria-pressed={follow} onClick={() => setFollow(!follow)} title="鏡頭跟著火車">
              <span className="ico">🚂</span>
              <span className="lbl">跟車</span>
            </button>
          )}
          <button className={prefs.log ? "on" : ""} aria-pressed={prefs.log} onClick={() => toggle("log")} title="村莊日誌：村民正在做什麼">
            <span className="ico">📜</span>
            <span className="lbl">日誌</span>
            {fresh && <i className="isl-dot" />}
          </button>
        </div>
      </div>
      {prefs.log && (
        <aside className="isl-log" aria-label="村莊日誌">
          <div className="isl-log-head">
            <div>
              <div className="eyebrow">VILLAGE LOG</div>
              <strong>村莊日誌</strong>
            </div>
            <span className="live">
              <i />
              LIVE
            </span>
            <button className="link" onClick={onClearLog} title="清空">
              清空
            </button>
            <button className="link" onClick={() => toggle("log")} aria-label="關閉日誌">
              ✕
            </button>
          </div>
          <ol className="isl-log-list">
            {log.length === 0 && <li className="muted empty">還沒有動靜。問一個問題，看看村民怎麼合作。</li>}
            {log.map((l) => (
              <li key={l.key} className={l.kind}>
                <button type="button" disabled={!l.agent} onClick={() => l.agent && onFocus(l.agent)} title={l.agent ? "鏡頭移過去" : undefined}>
                  <span className="t">{l.time}</span>
                  <span className="i">{l.icon}</span>
                  <span className="x">{l.text}</span>
                </button>
              </li>
            ))}
          </ol>
        </aside>
      )}
    </>
  );
}
