import type { VillageEvent } from "../events";
import type { VillagerInfo } from "../village/layout";

/** 登入頁的示範村民（不需要登入、不呼叫後端） */
export const DEMO_VILLAGERS: VillagerInfo[] = [
  { id: "poet", name: "詩人", sprite: "poet", home: [7, 6] },
  { id: "jester", name: "笑話師", sprite: "jester", home: [22, 6] },
  { id: "translator", name: "翻譯官", sprite: "translator", home: [18, 15] },
  { id: "calculator", name: "計算機", sprite: "merchant", home: [24, 12] },
  { id: "chart", name: "圖表師", sprite: "scholar", home: [8, 13] },
  // 駐村專家：住在鐵道沿線的小站，東西由火車經過中轉站送
  { id: "web-research", name: "網路研究員", sprite: "merchant", home: null, remote: true },
  { id: "newspaper", name: "報社", sprite: "scholar", home: null, remote: true },
];

/** 一步：等 wait 毫秒後送出事件並更新字幕 */
interface Beat {
  wait: number;
  caption?: string;
  events?: Partial<VillageEvent>[];
}

const ev = (type: string, agent?: string, extra: Record<string, unknown> = {}) => ({ type, agent, ...extra });
const handoff = (from: string, to: string) => ev("agent.handoff", undefined, { from_agent: from, to_agent: to });

const done = ev("run.finished");

/** 五段劇本輪流播放，展示村落的特色：分工接力、駐村專家與火車、工具精準、平行工作、對話打造村民、審核把關 */
const SCENES: Beat[][] = [
  [
    { wait: 600, caption: "💬 有人問：「寫一首關於海的詩，再把它改成笑話」", events: [ev("run.started", "mayor")] },
    { wait: 2200, caption: "🧓 村長讀了村民名冊，分派：詩人 → 笑話師", events: [ev("plan.created", "mayor"), handoff("mayor", "poet")] },
    { wait: 1400, caption: "✍️ 詩人開始寫詩…", events: [ev("agent.started", "poet")] },
    { wait: 2600, caption: "📜 詩人寫好了，把詩交給笑話師", events: [ev("agent.finished", "poet"), handoff("poet", "jester")] },
    { wait: 2000, caption: "🤡 笑話師把詩改編成笑話…", events: [ev("agent.started", "jester")] },
    { wait: 2400, caption: "🔍 審核員檢查：有沒有做到你要求的事？", events: [ev("agent.finished", "jester"), ev("review.started", "reviewer")] },
    { wait: 1800, caption: "✅ 審核通過，答案交給你！", events: [ev("review.finished", "reviewer", { passed: true }), done] },
  ],
  [
    { wait: 1500, caption: "💬 有人問：「查最近的 AI 新聞，做成一份報紙」", events: [ev("run.started", "mayor")] },
    { wait: 2000, caption: "🧓 村長分派：網路研究員 → 報社（兩位都是住在鐵道沿線的駐村專家）", events: [ev("plan.created", "mayor")] },
    { wait: 1500, caption: "🚂 火車把村長的指示送到網路研究員站", events: [ev("agent.started", "web-research")] },
    { wait: 9000, caption: "🔎 網路研究員查好了，火車去載成果，送進車站旁的中轉站", events: [ev("agent.finished", "web-research")] },
    { wait: 1200, caption: "📦 中轉站的包裹貼上號碼，再由火車載到報社站", events: [handoff("web-research", "newspaper")] },
    { wait: 1500, events: [ev("agent.started", "newspaper")] },
    { wait: 15000, caption: "📰 報社排好版面，火車把報紙載回中轉站", events: [ev("agent.finished", "newspaper", { attachments: [{ kind: "file", name: "村莊日報.pdf" }] })] },
    { wait: 9000, caption: "✅ 報紙送到你手上！", events: [done] },
  ],
  [
    { wait: 1500, caption: "💬 有人問：「一杯 65 元，買 7 杯打 9 折要多少錢？」", events: [ev("run.started", "mayor")] },
    { wait: 2000, caption: "🧓 村長請計算機幫忙", events: [ev("plan.created", "mayor"), handoff("mayor", "calculator")] },
    { wait: 1500, caption: "🧮 計算機把問題轉成算式 65×7×0.9，交給程式精準計算", events: [ev("agent.started", "calculator")] },
    { wait: 2400, caption: "✅ 409.5 元 —— 工具型村民用程式計算，弱模型也不會算錯", events: [ev("agent.finished", "calculator"), done] },
  ],
  [
    { wait: 1500, caption: "💬 有人問：「把銷售數字畫成圖，另外把摘要翻成英文」", events: [ev("run.started", "mayor")] },
    {
      wait: 2000,
      caption: "⚡ 兩件事互不相關 —— 圖表師和翻譯官同時開工",
      events: [ev("plan.created", "mayor"), ev("agent.started", "chart"), ev("agent.started", "translator")],
    },
    { wait: 2600, caption: "📊 圖表師畫好了長條圖", events: [ev("agent.finished", "chart")] },
    { wait: 1200, caption: "🌐 翻譯官也完成了，兩份成果一起交出", events: [ev("agent.finished", "translator"), done] },
  ],
  [
    { wait: 1500, caption: "🔨 想要新幫手？跟工匠聊聊你想要什麼樣的村民", events: [ev("workshop.started", "craftsman")] },
    { wait: 2800, caption: "🛠️ 工匠用日常的問題問清楚，再幫你打造第一版", events: [ev("workshop.finished", "craftsman")] },
    { wait: 2200, caption: "🏘️ 確認後新村民就會搬進村子，馬上可以派工作" },
  ],
];

/** 開始播放；回傳停止函式 */
export function playDemo(dispatch: (e: VillageEvent) => void, setCaption: (c: string) => void): () => void {
  let stopped = false;
  let timer: number | undefined;
  let scene = 0;
  let beat = 0;

  const next = () => {
    if (stopped) return;
    const beats = SCENES[scene];
    if (beat >= beats.length) {
      scene = (scene + 1) % SCENES.length;
      beat = 0;
      timer = window.setTimeout(next, 2500); // 場景之間讓村民閒晃一下
      return;
    }
    const b = beats[beat++];
    timer = window.setTimeout(() => {
      if (stopped) return;
      if (b.caption) setCaption(b.caption);
      for (const e of b.events ?? []) dispatch({ ts: "", ...e } as VillageEvent);
      next();
    }, b.wait);
  };
  next();
  return () => {
    stopped = true;
    window.clearTimeout(timer);
  };
}
