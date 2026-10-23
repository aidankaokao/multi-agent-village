/**
 * 3D 村子共用的東西：座標換算、日夜關鍵影格、搬運圖示、協作線、障礙物、生活作息的地點，
 * 以及「場景」（Terrain）的介面——島嶼（island/IslandTerrain.ts）、之後的鐵道小鎮各自實作一份。
 */
import * as THREE from "three";
import type { Pose, SpotKind } from "../village/life";
import type { Villager3D } from "./Villager3D";
import { MAP_H, MAP_W } from "../village/VillageScene";

export const S = 0.9; // 像素地圖一格 = 0.9 單位
export const SPEED = 2.4; // 單位 / 秒
export const TAG_Y = 1.85; // 名牌掛在頭上多高（村民自己的座標，會跟著放大）
export const VILLAGER_SCALE = 1.45; // 村民比例放大一點，遠看才認得出來

/** 像素地圖的格子座標 → 3D 世界座標（村子的中心在原點） */
export function toWorld(tx: number, ty: number): THREE.Vector3 {
  return new THREE.Vector3((tx - MAP_W / 2) * S, 0.18, (ty - MAP_H / 2) * S);
}

export const pick = <T,>(xs: T[]): T => xs[Math.floor(Math.random() * xs.length)];
export const between = ([a, b]: [number, number]) => a + Math.random() * (b - a);

// ---------------------------------------------------------------- 生活作息的地點

export interface Spot {
  kind: SpotKind;
  pos: THREE.Vector3;
  face: THREE.Vector3; // 做事時面向哪裡
  taken: string | null; // 誰佔著
}

// 坐著做的事（坐下：身體往下、腳往前伸）
export const SITTING: Pose[] = ["sit", "fish", "write", "type", "eat", "sleep"];

// ---------------------------------------------------------------- 駐村專家住的地方

/** 駐村專家住的地方（島嶼版是外圍小島，鐵道小鎮是沿線的小站） */
export interface Outpost {
  center: THREE.Vector3;
  radius: number; // 站在這個範圍內就算在那裡
  gates: THREE.Vector3[]; // 從這裡走到村子要經過的點（島嶼：橋的兩端，由外往內）
  door: Spot; // 小屋門口（晚上在這裡打瞌睡）
  desk: Spot; // 做事的地方（駐村專家不到村子裡做生活上的事）
}

// ---------------------------------------------------------------- 日夜（依真實時間；分鐘）

// [分鐘, 天空, 太陽光色, 太陽強度, 天光, 地光, 天地光強度, 夜晚程度 0～1, 海水]
export type Key = [number, number, number, number, number, number, number, number, number];
export const DAY: Key[] = [
  [0, 0x1b2550, 0xa8b8ff, 0.45, 0x8a9ad0, 0x3a3448, 0.8, 1, 0x2b5482],
  [300, 0x24305e, 0xb0bcff, 0.45, 0x8a96cc, 0x3e3850, 0.8, 1, 0x2f5886],
  [360, 0xf2b8a0, 0xffb48a, 1.3, 0xf3c6b5, 0x6b5a5a, 1.0, 0.45, 0x8fb7c4],
  [450, 0xbfe0f0, 0xffe2c4, 2.4, 0xeaf2f6, 0x8a7a68, 1.25, 0, 0x8ccbd2],
  [720, 0xb9dcee, 0xfff1de, 2.7, 0xeef6fa, 0x8a7a68, 1.35, 0, 0x7ec7d0],
  [960, 0xbcdcec, 0xffe3c0, 2.5, 0xf1efe8, 0x8a7a66, 1.3, 0, 0x80c4cc],
  [1065, 0xf0b48c, 0xff9f6a, 1.7, 0xf6d2bc, 0x7d6660, 1.15, 0.3, 0x8fb2cc],
  [1140, 0x7a6aa8, 0xff9a7a, 0.9, 0xc8b8e4, 0x5d4f60, 0.9, 0.7, 0x5b7ba8],
  [1230, 0x2a3260, 0xa8b8ff, 0.45, 0x8a92c8, 0x3e3850, 0.8, 1, 0x2d5480],
  [1440, 0x1b2550, 0xa8b8ff, 0.45, 0x8a9ad0, 0x3a3448, 0.8, 1, 0x2b5482],
];

/** 預覽用的時段（網址 ?time=dawn|day|dusk|night 或 HH:MM） */
export function parseTime(q: string | null): number | null {
  if (!q) return null;
  const named: Record<string, number> = { dawn: 380, morning: 480, day: 600, noon: 720, dusk: 1100, evening: 1140, night: 1320 };
  if (q in named) return named[q];
  const m = /^(\d{1,2}):(\d{2})$/.exec(q);
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

// ---------------------------------------------------------------- 搬運與協作線

// 交接時頭上拿的東西：看得到產出就照產出（PDF、圖、圖表、文字檔），不然照村民的外觀
export const CARRY_BY_SPRITE: Record<string, string> = {
  mayor: "📋", poet: "📜", jester: "😄", translator: "🔤", scholar: "📚", merchant: "💰", farmer: "🌾", chef: "🍲",
  painter: "🎨", typist: "📝", reviewer: "✏️", auditor: "✅", scribe: "📒", appraiser: "🔍", craftsman: "🔨",
};

export function carryFromAttachments(raw: unknown): string | null {
  if (!Array.isArray(raw) || !raw.length) return null;
  const kinds = raw.map((a) => (a && typeof a === "object" ? (a as { kind?: string; name?: string }) : {}));
  if (kinds.some((a) => a.kind === "image")) return "🖼️";
  if (kinds.some((a) => a.kind === "svg")) return "📊";
  if (kinds.some((a) => (a.name ?? "").toLowerCase().endsWith(".pdf"))) return "📄";
  if (kinds.some((a) => a.kind === "file")) return "📝";
  return null;
}

export const FLOW_COLORS = [0xe8643c, 0x3a8fd0, 0xe0a43a, 0x8a5ad0, 0x3fa05a];
export const FLOW_MAX_MS = 10 * 60_000; // 有 run_id：最多留到問答結束（保險起見 10 分鐘）
export const FLOW_ORPHAN_MS = 20_000; // 別人的問答（沒有 run_id）：20 秒後淡出
export const FLOW_LINGER_MS = 5_000; // 問答結束後再留 5 秒

export interface Flow {
  curve: THREE.QuadraticBezierCurve3;
  line: THREE.Mesh;
  dot: THREE.Mesh;
  runId?: string;
  born: number;
  fadeAt: number;
}

// ---------------------------------------------------------------- 障礙物

export const BLOCK_R: Record<string, number> = { tree: 0.55, pine: 0.55, bush: 0.75, rock: 0.6, well: 0.95, house_red: 2.0, house_blue: 2.0, sign: 0.45, workbench: 0.95 };

export interface Obstacle {
  x: number;
  z: number;
  r: number;
}


// ---------------------------------------------------------------- 場景

/** 場景蓋東西時可以用的工具（由 World3D 提供） */
export interface BuildCtx {
  scene: THREE.Scene;
  small: boolean; // 手機、小螢幕：少蓋一點
  addObstacle(x: number, z: number, r: number): void;
  blocked(x: number, z: number): boolean;
  addLamp(x: number, z: number): void; // 路燈（晚上亮）
}

/** 鏡頭的預設與限制 */
export interface ViewConfig {
  dist: number; // 預設距離
  portraitDist?: number; // 直式畫面（手機）的預設距離：場景比較寬時拉遠一點
  polar: number;
  azimuth: number;
  target: THREE.Vector3;
  minDist: number;
  maxDist: number;
  panLimit: number; // 鏡頭中心離原點最遠多少
  birdHeight: number;
  fog: [number, number];
}

/**
 * 一個場景：地形、裝飾、駐村專家住的地方。村民、日夜、協作線、事件都在 World3D，場景不用管。
 */
export interface Terrain {
  view: ViewConfig;
  /** 蓋場景（地面、裝飾、路燈…），障礙物用 ctx.addObstacle 登記 */
  build(ctx: BuildCtx): void;
  /** 生活作息的地點（build 之後呼叫，障礙物都登記好了） */
  spots(ctx: BuildCtx): Spot[];
  /** 村子的範圍內（離邊緣至少 margin） */
  inside(p: THREE.Vector3, margin: number): boolean;
  /** 繞路用的路口（直走會撞到東西時，經過這些點） */
  routeNodes: THREE.Vector3[];
  /** 第 index 位駐村專家住的地方（第一次需要時才蓋；who 用來挑樣式） */
  outpost(index: number, ctx: BuildCtx, who: { id: string; name: string; skill?: string }): { outpost: Outpost; group: THREE.Group };
  /** 駐村專家外出中（窗戶、路燈由 World3D 熄掉；場景可以再加別的，例如「暫停營業」看板） */
  outpostAway?(group: THREE.Group, away: boolean): void;
  /** 依時間換顏色（DAY 的「海水」那一欄） */
  tint(water: THREE.Color, night: number): void;
  /** 每一幀（船、雲） */
  update(dt: number, now: number): void;
  /** 交通工具（鐵道小鎮的火車）：有的話，和駐村專家之間的交接由它處理（build 之後才有） */
  courier?: Courier;
}

/** World3D 給交通工具用的東西 */
export interface CourierHost {
  /** 寫一句到村莊日誌 */
  log(icon: string, text: string, agent?: string, kind?: "info" | "ok" | "bad"): void;
  flowsVisible: boolean;
}

/**
 * 交通工具：場景自己決定東西怎麼送（例如放進中轉站、火車載到專家的小站）。
 * 沒有交通工具的場景（島嶼）照舊：村民走過去交給對方。
 */
export interface Courier {
  attach(host: CourierHost): void;
  /** 交接；回傳 true = 交通工具處理了（World3D 不再走路、不畫弧線） */
  handoff(from: Villager3D, to: Villager3D, icon: string, runId?: string): boolean;
  /** 村民開始工作（駐村專家沒有人交東西給它時，火車送村長的指示過去） */
  started(v: Villager3D, runId?: string): void;
  /** 村民做完（駐村專家的成果由火車載回中轉站） */
  finished(v: Villager3D, icon: string, runId?: string): void;
  /** 問答結束（中轉站清空） */
  runEnded(runId?: string): void;
  setFlowsVisible(v: boolean): void;
  update(dt: number, now: number): void;
  dispose(): void;
}
