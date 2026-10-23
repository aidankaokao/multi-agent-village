/**
 * 島嶼版的村子（Three.js）：和像素版（village/VillageScene.ts）吃一樣的村民名單與事件，畫法不同。
 *
 * - 主島：像素地圖的 30×20 格對應到一座圓角六邊形的島；裝飾、池塘、小路的位置和像素版一樣
 * - 駐村專家住在外圍的小島，用木棧橋連到主島；交接時沿著橋走過來
 * - 名牌、冒泡、狀態用 HTML（CSS2DRenderer），文字清楚、不受 3D 縮放影響
 * - 村民的行為（冒一句、兩人聊天、派工、交接、外出中）照像素版；閒著時照生活作息（village/life.ts）
 *   走去井邊、池塘、樹下做事，派工時立刻中斷，做完再回去接著做
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import type { VillageEvent } from "../events";
import { APPRAISER, CRAFTSMAN, DECOR, MAP_H, MAP_W, MAYOR, POND, REVIEWER, SCRIBE, type VillagerInfo } from "../village/VillageScene";
import { nextActivity, periodOf, rolesOf, type Activity, type Pose, type SpotKind } from "../village/life";
import * as M from "./models";

const S = 0.9; // 像素地圖一格 = 0.9 單位
const ISLAND_R = 19.5; // 主島（平頂六邊形）的外接圓半徑
const ISLET_DIST = 27; // 小島中心離主島中心多遠
const ISLET_R = 3.6;
const SPEED = 2.4; // 單位 / 秒
const TAG_Y = 1.85; // 名牌掛在頭上多高（村民自己的座標，會跟著放大）
const VILLAGER_SCALE = 1.45; // 村民比例放大一點，遠看才認得出來

/** 像素地圖的格子座標 → 島上的世界座標 */
export function toWorld(tx: number, ty: number): THREE.Vector3 {
  return new THREE.Vector3((tx - MAP_W / 2) * S, 0.18, (ty - MAP_H / 2) * S);
}

/** 平頂六邊形在方向 a（弧度）上，中心到邊的距離 */
function hexReach(r: number, a: number): number {
  const sector = Math.PI / 3;
  const t = (((a % sector) + sector) % sector) - sector / 2;
  return (r * Math.cos(Math.PI / 6)) / Math.cos(t);
}

function insideHex(p: THREE.Vector3, r: number, margin: number): boolean {
  const d = Math.hypot(p.x, p.z);
  return d <= hexReach(r, Math.atan2(p.z, p.x)) - margin;
}

// 外圍小島的位置（角度）：先放在鏡頭看得清楚的前方與兩側
const ISLET_ANGLES = [20, -20, 60, -60, 100, -100, 140, -140, 180].map((d) => (d * Math.PI) / 180);

interface Islet {
  center: THREE.Vector3;
  gateIslet: THREE.Vector3; // 橋在小島這端
  gateIsland: THREE.Vector3; // 橋在主島這端
  door: Spot; // 小屋門口（晚上在這裡打瞌睡）
  desk: Spot; // 小島上做事的地方（駐村專家不到主島做生活上的事）
}

// ---------------------------------------------------------------- 生活作息的地點

interface Spot {
  kind: SpotKind;
  pos: THREE.Vector3;
  face: THREE.Vector3; // 做事時面向哪裡
  taken: string | null; // 誰佔著
}

// 坐著做的事（坐下：身體往下、腳往前伸）
const SITTING: Pose[] = ["sit", "fish", "write", "type", "eat", "sleep"];

// ---------------------------------------------------------------- 日夜（依真實時間；分鐘）

// [分鐘, 天空, 太陽光色, 太陽強度, 天光, 地光, 天地光強度, 夜晚程度 0～1, 海水]
type Key = [number, number, number, number, number, number, number, number, number];
const DAY: Key[] = [
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
const CARRY_BY_SPRITE: Record<string, string> = {
  mayor: "📋", poet: "📜", jester: "😄", translator: "🔤", scholar: "📚", merchant: "💰", farmer: "🌾", chef: "🍲",
  painter: "🎨", typist: "📝", reviewer: "✏️", auditor: "✅", scribe: "📒", appraiser: "🔍", craftsman: "🔨",
};

function carryFromAttachments(raw: unknown): string | null {
  if (!Array.isArray(raw) || !raw.length) return null;
  const kinds = raw.map((a) => (a && typeof a === "object" ? (a as { kind?: string; name?: string }) : {}));
  if (kinds.some((a) => a.kind === "image")) return "🖼️";
  if (kinds.some((a) => a.kind === "svg")) return "📊";
  if (kinds.some((a) => (a.name ?? "").toLowerCase().endsWith(".pdf"))) return "📄";
  if (kinds.some((a) => a.kind === "file")) return "📝";
  return null;
}

const FLOW_COLORS = [0xe8643c, 0x3a8fd0, 0xe0a43a, 0x8a5ad0, 0x3fa05a];
const FLOW_MAX_MS = 10 * 60_000; // 有 run_id：最多留到問答結束（保險起見 10 分鐘）
const FLOW_ORPHAN_MS = 20_000; // 別人的問答（沒有 run_id）：20 秒後淡出
const FLOW_LINGER_MS = 5_000; // 問答結束後再留 5 秒

interface Flow {
  curve: THREE.QuadraticBezierCurve3;
  line: THREE.Mesh;
  dot: THREE.Mesh;
  runId?: string;
  born: number;
  fadeAt: number;
}

// ---------------------------------------------------------------- 障礙物

const BLOCK_R: Record<string, number> = { tree: 0.55, pine: 0.55, bush: 0.75, rock: 0.6, well: 0.95, house_red: 2.0, house_blue: 2.0, sign: 0.45, workbench: 0.95 };

interface Obstacle {
  x: number;
  z: number;
  r: number;
}

// ---------------------------------------------------------------- 村民

type Bubble = "working" | "done" | "error" | "thinking";
const BUBBLE: Record<Bubble, string> = { working: "⚙️", thinking: "💭", done: "✅", error: "❗" };

class Villager3D {
  root: THREE.Group;
  private model: M.Chibi;
  private tag: CSS2DObject;
  private nameEl: HTMLDivElement;
  private speechEl: HTMLDivElement;
  private statusEl: HTMLDivElement;
  private carryEl: HTMLDivElement;
  private carryTimer?: number;
  private path: THREE.Vector3[] = [];
  private onArrive?: () => void;
  private walkT = 0;
  private wanderAt = 0;
  private timers: number[] = [];
  private speechTimer?: number;
  private statusTimer?: number;
  private appearT = -1;
  // 生活作息
  activity: Activity | null = null; // 正在做（或正要走去做）的事
  private spot: Spot | null = null;
  private pose: Pose | null = null; // 到了地點才擺姿勢
  private propObj: THREE.Group | null = null;
  private lastAct: string | null = null;
  private resume: Activity | null = null; // 被派工打斷的事，做完回去接著做
  private actEl: HTMLDivElement;
  private actTimer?: number;
  roles: string[] = [];
  busy = false;
  chatting = false;
  away = false;
  lines: string[] = [];
  skill = "";
  speaking = false;

  constructor(
    private world: IslandWorld,
    public id: string,
    public name: string,
    public sprite: string,
    public home: THREE.Vector3,
    public islet: Islet | null,
    public remote: boolean,
  ) {
    this.model = M.chibi(sprite);
    this.root = this.model.root;
    this.root.position.copy(home);
    this.root.scale.setScalar(VILLAGER_SCALE);
    this.root.rotation.y = Math.random() * Math.PI * 2;
    this.root.userData.villagerId = id;
    const el = document.createElement("div");
    el.className = "isl-tag";
    this.speechEl = document.createElement("div");
    this.speechEl.className = "isl-speech";
    this.statusEl = document.createElement("div");
    this.statusEl.className = "isl-status";
    this.carryEl = document.createElement("div");
    this.carryEl.className = "isl-carry";
    this.actEl = document.createElement("div");
    this.actEl.className = "isl-act";
    this.nameEl = document.createElement("div");
    this.nameEl.className = "isl-name";
    this.nameEl.textContent = name;
    el.append(this.speechEl, this.statusEl, this.carryEl, this.actEl, this.nameEl);
    this.tag = new CSS2DObject(el);
    this.tag.center.set(0.5, 1);
    this.tag.position.set(0, TAG_Y, 0);
    this.root.add(this.tag);
    this.wanderAt = performance.now() + 300 + Math.random() * 2200;
  }

  get idle(): boolean {
    return !this.busy && !this.chatting && !this.away;
  }

  /** 可以被找去聊天：閒著，而且沒在做事（或做的是可以聊天的事，例如在廣場喝茶） */
  get chatty(): boolean {
    return this.idle && (!this.activity || (!!this.activity.social && !!this.pose));
  }

  get sleeping(): boolean {
    return this.pose === "sleep";
  }

  /** 做事時偶爾冒的一句（沒有就用名片的台詞） */
  ambientLine(): string | null {
    if (this.sleeping) return null;
    const own = this.pose ? this.activity?.lines ?? [] : [];
    if (own.length && (!this.lines.length || Math.random() < 0.6)) return pick(own);
    return this.lines.length ? pick(this.lines) : null;
  }

  get pos(): THREE.Vector3 {
    return this.root.position;
  }

  get area(): Islet | null {
    return this.islet;
  }

  setNameVisible(v: boolean) {
    this.nameEl.style.display = v ? "" : "none";
  }

  /** 每一幀：走路、擺手腳、轉向、登場動畫 */
  update(dt: number, now: number) {
    const p = this.root.position;
    if (this.path.length) {
      const target = this.path[0];
      const d = new THREE.Vector3(target.x - p.x, 0, target.z - p.z);
      const dist = d.length();
      const step = SPEED * dt;
      if (dist <= step) {
        p.x = target.x;
        p.z = target.z;
        this.path.shift();
        if (!this.path.length) {
          const cb = this.onArrive;
          this.onArrive = undefined;
          cb?.();
        }
      } else {
        d.setLength(step);
        p.add(d);
        this.turnTo(Math.atan2(d.x, d.z), dt);
      }
      this.walkT += dt * 9;
    } else {
      this.walkT = 0;
    }
    if (this.path.length || !this.pose) {
      const swing = this.path.length ? Math.sin(this.walkT) * 0.5 : 0;
      this.setLimbs(swing, -swing, -swing * 0.8, swing * 0.8);
      this.model.body.position.y = this.path.length ? Math.abs(Math.sin(this.walkT)) * 0.06 : Math.sin(now / 600 + p.x) * 0.012;
      this.model.body.rotation.set(0, 0, 0);
    } else {
      this.animatePose(this.pose, now);
    }
    if (this.appearT >= 0) {
      this.appearT = Math.min(1, this.appearT + dt * 1.6);
      const t = this.appearT;
      this.root.scale.setScalar(VILLAGER_SCALE * (t < 1 ? 1.15 * (1 - Math.pow(1 - t, 3)) : 1));
      if (t >= 1) this.appearT = -1;
    }
    if (!this.busy && !this.chatting && !this.path.length && now >= this.wanderAt) {
      if (this.world.lifeOn && !this.away) this.live(now);
      else this.wander(now);
    }
  }

  private setLimbs(l0: number, l1: number, a0: number, a1: number) {
    this.model.legs[0].rotation.x = l0;
    this.model.legs[1].rotation.x = l1;
    this.model.arms[0].rotation.x = a0;
    this.model.arms[1].rotation.x = a1;
  }

  /** 做事的姿勢（每一幀）：手腳從關節轉，身體坐下 / 前傾 / 左右晃 */
  private animatePose(pose: Pose, now: number) {
    const b = this.model.body;
    const t = now / 1000 + this.pos.x; // 每個人錯開一點
    const sitting = SITTING.includes(pose) || (pose === "read" && this.spot?.kind === "tree");
    const leg = sitting ? -1.45 : 0;
    b.position.y = sitting ? -0.2 : 0;
    b.rotation.set(0, 0, 0);
    switch (pose) {
      case "fish": {
        const tug = Math.sin(t * 0.7) > 0.93 ? 0.25 : 0; // 偶爾有魚咬餌
        this.setLimbs(leg, leg, -0.9 - tug, -0.9 - tug);
        break;
      }
      case "write":
        this.setLimbs(leg, leg, -0.7, -0.85 + Math.sin(t * 9) * 0.12);
        b.rotation.x = 0.12;
        break;
      case "type":
        this.setLimbs(leg, leg, -0.85 + Math.sin(t * 11) * 0.1, -0.85 - Math.sin(t * 11) * 0.1);
        b.rotation.x = 0.08;
        break;
      case "read":
        this.setLimbs(leg, leg, -1.0, -1.0);
        b.rotation.x = 0.06 + Math.sin(t * 0.8) * 0.03;
        break;
      case "eat":
        this.setLimbs(leg, leg, -0.3, -1.1 - Math.max(0, Math.sin(t * 2)) * 0.9);
        break;
      case "sleep":
        this.setLimbs(leg, leg, 0, 0);
        b.rotation.x = 0.28 + Math.sin(t * 0.9) * 0.05;
        break;
      case "sit":
        this.setLimbs(leg, leg, -0.3, -0.3);
        b.position.y += Math.sin(t * 1.4) * 0.008;
        break;
      case "hammer":
        this.setLimbs(0, 0, -0.6, -0.9 - Math.max(0, Math.sin(t * 7)) * 1.3);
        b.position.y = Math.abs(Math.sin(t * 7)) * 0.02;
        break;
      case "water":
        this.setLimbs(0, 0, -0.3, -1.0);
        b.rotation.x = 0.12;
        break;
      case "stretch":
        this.setLimbs(0, 0, -2.9 + Math.sin(t * 2) * 0.2, -2.9 + Math.sin(t * 2) * 0.2);
        b.position.y = Math.max(0, Math.sin(t * 2)) * 0.06;
        break;
      case "look":
        this.setLimbs(0, 0, this.propObj ? -1.4 : 0, this.propObj ? -1.4 : 0.2);
        b.rotation.y = Math.sin(t * 0.6) * 0.5;
        break;
      case "sweep":
        this.setLimbs(0, 0, -0.6, -0.6);
        b.rotation.y = Math.sin(t * 3.2) * 0.35;
        break;
      case "paint":
        this.setLimbs(0, 0, -0.4, -1.2 + Math.sin(t * 4) * 0.15);
        break;
      case "dance": {
        const s = Math.sin(t * 6);
        this.setLimbs(s * 0.3, -s * 0.3, -2.2 + s * 0.6, -2.2 - s * 0.6);
        b.position.y = Math.abs(Math.sin(t * 6)) * 0.1;
        break;
      }
      default: // stand（喝茶時偶爾喝一口）
        this.setLimbs(0, 0, 0, this.propObj ? -0.9 - Math.max(0, Math.sin(t * 0.9)) * 0.5 : 0);
    }
  }

  // ---------------------------------------------------------------- 生活作息

  /** 該換下一件事了：剛才被打斷的先接著做，不然照時段和角色挑一件（null = 隨便走走） */
  private live(now: number) {
    this.endActivity();
    const period = periodOf(this.world.minutesNow());
    let act = this.resume;
    this.resume = null;
    if (act && (period === "night") !== (act.id === "sleep")) act = null; // 天亮了就不回去睡、天黑了就不回去做事
    act ??= nextActivity(this.roles, period, this.lastAct);
    if (!act) return this.wander(now);
    const spot = this.world.claimSpot(this, act.spot);
    if (!spot) return this.wander(now);
    this.activity = act;
    this.spot = spot;
    this.lastAct = act.id;
    this.wanderAt = Infinity; // 到了才開始計時
    this.goTo(spot.pos, () => {
      if (this.activity !== act || this.busy) return;
      this.faceTo(spot.face);
      this.pose = act.pose;
      if (act.prop) {
        this.propObj = M.prop(act.prop);
        this.model.body.add(this.propObj);
      }
      this.showAct(act.icon);
      this.wanderAt = performance.now() + between(act.dur) * 1000;
    });
  }

  /** 走過去（主島上會繞開池塘、房子；駐村專家在自己的小島上） */
  private goTo(target: THREE.Vector3, onDone: () => void) {
    if (this.islet) return this.walkTo(target, onDone, this.islet);
    this.path = this.world.route(this.pos, target);
    this.onArrive = onDone;
  }

  /** 收起道具、讓出地點 */
  endActivity() {
    if (this.wanderAt === Infinity) this.wanderAt = performance.now() + 1000; // 走到一半被打斷
    if (this.propObj) M.disposeProp(this.propObj);
    this.propObj = null;
    this.pose = null;
    this.activity = null;
    this.world.releaseSpot(this.spot);
    this.spot = null;
    this.hideAct();
  }

  /** 被找去做別的事：記住剛才在做什麼，做完回去接著做；被叫醒的會說一聲 */
  private interrupt(): boolean {
    const wasSleeping = this.sleeping;
    if (this.activity) this.resume = this.activity;
    const had = !!this.activity;
    this.endActivity();
    if (had) this.flashAlert();
    if (wasSleeping) this.say(pick(["來了來了！", "哈啊…我醒了", "誰叫我？"]), 1800);
    return had;
  }

  private showAct(icon: string) {
    window.clearTimeout(this.actTimer);
    this.actEl.textContent = icon;
    this.actEl.className = "isl-act show";
  }

  private hideAct() {
    window.clearTimeout(this.actTimer);
    this.actEl.className = "isl-act";
  }

  private flashAlert() {
    this.actEl.textContent = "❗";
    this.actEl.className = "isl-act show alert";
    window.clearTimeout(this.actTimer);
    this.actTimer = window.setTimeout(() => (this.actEl.className = "isl-act"), 900);
  }

  /** 時段改了、生活作息關掉：做到一半的事先收起來，馬上重挑 */
  restartLife() {
    if (!this.activity || this.busy || this.chatting) return;
    this.endActivity();
    this.stop();
    this.wanderAt = performance.now() + Math.random() * 1500;
  }

  private turnTo(yaw: number, dt: number) {
    let diff = yaw - this.root.rotation.y;
    diff = Math.atan2(Math.sin(diff), Math.cos(diff));
    this.root.rotation.y += diff * Math.min(1, dt * 10);
  }

  faceTo(p: THREE.Vector3) {
    this.root.rotation.y = Math.atan2(p.x - this.pos.x, p.z - this.pos.z);
  }

  /** 走一段路（會經過橋）；到了呼叫 onDone */
  walkTo(target: THREE.Vector3, onDone?: () => void, targetIslet: Islet | null = null) {
    const points: THREE.Vector3[] = [];
    const from = this.currentIslet();
    if (from && from !== targetIslet) points.push(from.gateIslet.clone(), from.gateIsland.clone());
    if (targetIslet && targetIslet !== from) points.push(targetIslet.gateIsland.clone(), targetIslet.gateIslet.clone());
    points.push(target.clone());
    this.path = points;
    this.onArrive = onDone;
  }

  /** 現在站在哪座小島上（在主島上回 null） */
  private currentIslet(): Islet | null {
    return this.world.isletAt(this.pos);
  }

  private wander(now: number) {
    this.wanderAt = now + 1500 + Math.random() * 3000;
    if (this.away) return;
    const target = this.world.wanderTarget(this.home, this.islet, this.pos);
    if (target) this.walkTo(target, undefined, this.islet);
  }

  stop() {
    this.path = [];
    this.onArrive = undefined;
  }

  say(text: string, ms = 3200) {
    window.clearTimeout(this.speechTimer);
    this.speechEl.textContent = text;
    this.speechEl.classList.add("show");
    this.speaking = true;
    this.speechTimer = window.setTimeout(() => {
      this.speechEl.classList.remove("show");
      this.speaking = false;
    }, ms);
  }

  greet(text: string) {
    if (!this.busy && !this.chatting && !this.pose) {
      this.stop();
      this.faceTo(this.world.cameraGround());
      this.wanderAt = performance.now() + 4000;
    }
    this.say(text, 2600);
  }

  showBubble(kind: Bubble, ms?: number) {
    window.clearTimeout(this.statusTimer);
    this.statusEl.textContent = BUBBLE[kind];
    this.statusEl.className = `isl-status show ${kind}`;
    if (ms) this.statusTimer = window.setTimeout(() => (this.statusEl.className = "isl-status"), ms);
  }

  startWork(kind: "working" | "thinking" = "working") {
    this.endChat();
    this.interrupt();
    this.busy = true;
    this.stop();
    this.faceTo(this.world.cameraGround());
    this.showBubble(kind);
  }

  finish(ok: boolean) {
    this.showBubble(ok ? "done" : "error", ok ? 1800 : 4000);
    this.busy = false;
    this.wanderAt = performance.now() + 2000;
  }

  chatWith(other: Villager3D, script: [string, string, string?]) {
    this.interrupt();
    if (!other.pose || !other.activity?.social) other.interrupt(); // 在廣場喝茶的就邊喝邊聊
    this.chatting = other.chatting = true;
    other.stop();
    other.faceTo(this.pos);
    const side = this.pos.x < other.pos.x ? -1 : 1;
    const spot = other.pos.clone().add(new THREE.Vector3(side * 1.2, 0, 0.2));
    this.walkTo(spot, () => {
      if (!this.chatting) return;
      this.faceTo(other.pos);
      other.faceTo(this.pos);
      const lines: [Villager3D, string | undefined][] = [[this, script[0]], [other, script[1]], [this, script[2]]];
      lines.forEach(([who, text], i) => {
        if (text) this.timers.push(window.setTimeout(() => who.say(text, 1800), i * 1700));
      });
      this.timers.push(window.setTimeout(() => {
        this.endChat();
        other.endChat();
      }, lines.filter((l) => l[1]).length * 1700 + 600));
    }, other.islet);
  }

  endChat() {
    this.timers.forEach((t) => window.clearTimeout(t));
    this.timers = [];
    if (!this.chatting) return;
    this.chatting = false;
    if (!this.busy) this.wanderAt = performance.now() + 800 + Math.random() * 1700;
  }

  /** 頭上顯示拿著的東西（交接用）；ms 之後收起來 */
  carry(icon: string | null, ms?: number) {
    window.clearTimeout(this.carryTimer);
    if (!icon) {
      this.carryEl.className = "isl-carry";
      return;
    }
    this.carryEl.textContent = icon;
    this.carryEl.className = "isl-carry show";
    if (ms) this.carryTimer = window.setTimeout(() => (this.carryEl.className = "isl-carry"), ms);
  }

  /** 走到另一位村民旁邊交接（頭上拿著 icon），交給對方後回家 */
  handoffTo(other: Villager3D, icon: string) {
    this.endChat();
    other.endChat();
    this.interrupt();
    other.interrupt();
    this.busy = true;
    this.carry(icon);
    const dir = new THREE.Vector3(this.pos.x - other.pos.x, 0, this.pos.z - other.pos.z);
    if (dir.lengthSq() < 0.01) dir.set(1, 0, 0);
    const spot = other.pos.clone().add(dir.setLength(1.1));
    this.walkTo(spot, () => {
      this.faceTo(other.pos);
      other.faceTo(this.pos);
      this.carry(null);
      other.carry(icon, 2200); // 東西到了對方手上
      this.timers.push(window.setTimeout(() => {
        this.busy = false;
        if (this.world.lifeOn) this.wanderAt = performance.now() + 300; // 回去接著做剛才的事
        else this.walkTo(this.world.wanderTarget(this.home, this.islet, this.home) ?? this.home.clone(), undefined, this.islet);
      }, 800));
    }, other.islet);
  }

  /** 被日誌點到：名牌閃一下 */
  greetPulse() {
    this.nameEl.classList.remove("pulse");
    void this.nameEl.offsetWidth;
    this.nameEl.classList.add("pulse");
  }

  setAway(away: boolean) {
    if (away === this.away) return;
    this.away = away;
    for (const m of this.model.materials) {
      m.transparent = away;
      m.opacity = away ? 0.35 : 1;
    }
    this.nameEl.textContent = away ? `${this.name}（外出中）` : this.name;
    this.nameEl.classList.toggle("away", away);
    if (away) {
      this.endActivity();
      this.resume = null;
      this.stop();
    }
  }

  appear() {
    this.appearT = 0;
    this.root.scale.setScalar(0.01);
    this.showBubble("done", 2000);
  }

  dispose() {
    this.endActivity();
    this.timers.forEach((t) => window.clearTimeout(t));
    window.clearTimeout(this.speechTimer);
    window.clearTimeout(this.statusTimer);
    window.clearTimeout(this.carryTimer);
    this.tag.element.remove();
    this.root.removeFromParent();
    this.root.traverse((o) => {
      if (o instanceof THREE.Mesh) o.geometry.dispose();
    });
    this.model.materials.forEach((m) => m.dispose());
  }
}

// ---------------------------------------------------------------- 閒聊（同像素版）

const AMBIENT_MS: [number, number] = [8000, 15000];
const CHAT_MS: [number, number] = [30000, 60000];
const MAX_SPEAKING = 2;
const CHAT_RANGE = 9;
const GREETINGS: [string, string, string?][] = [
  ["嗨！", "嗨～最近好嗎？", "還不錯！"],
  ["今天好忙啊", "我也是…"],
  ["天氣真好", "適合散步～"],
  ["有人找你喔", "真的嗎？我去看看"],
  ["吃飽了嗎？", "剛吃完！"],
];
const pick = <T,>(xs: T[]): T => xs[Math.floor(Math.random() * xs.length)];
const between = ([a, b]: [number, number]) => a + Math.random() * (b - a);

function chatScript(b: Villager3D): [string, string, string?] {
  if (b.skill && Math.random() < 0.6) {
    const ask = `${b.name}，你會${b.skill}對吧？`;
    return [ask.length > 16 ? `你會${b.skill}對吧？` : ask, "對啊，交給我！", "下次找你幫忙"];
  }
  return pick(GREETINGS);
}

// ---------------------------------------------------------------- 世界

export interface IslandVillager extends VillagerInfo {
  remote?: boolean;
}

export class IslandWorld {
  onVillagerClick?: (id: string) => void;
  private renderer: THREE.WebGLRenderer;
  private labels: CSS2DRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private controls: OrbitControls;
  private villagers = new Map<string, Villager3D>();
  private looks = new Map<string, string>();
  private islets = new Map<string, Islet>(); // 駐村專家 id → 小島
  private isletGroups = new Map<string, THREE.Group>();
  private obstacles: Obstacle[] = [];
  private spots: Spot[] = []; // 主島上做事的地點
  lifeOn = true;
  private boats: THREE.Group[] = [];
  // 日夜
  private hemi!: THREE.HemisphereLight;
  private sun!: THREE.DirectionalLight;
  private seaMat!: THREE.MeshStandardMaterial;
  private glowMats: { mat: THREE.MeshStandardMaterial; k: number }[] = []; // 窗戶、路燈：晚上發光
  private lampLights: THREE.PointLight[] = [];
  private stars!: THREE.Points;
  private timeOverride: number | null = null;
  private timeTimer = 0;
  night = 0;
  // 搬運與協作線
  private carrying = new Map<string, string>(); // 村民 id → 交接時拿著的東西
  private flows: Flow[] = [];
  // 工具列
  private small = false;
  paused = false;
  private flowsVisible = true;
  private camAnim: { fromPos: THREE.Vector3; toPos: THREE.Vector3; fromT: THREE.Vector3; toT: THREE.Vector3; t: number } | null = null;
  private clouds: THREE.Group[] = [];
  private frame = 0;
  private last = performance.now();
  private nextAmbient = performance.now() + between(AMBIENT_MS);
  private nextChat = performance.now() + between(CHAT_MS);
  private idleTimer: number;
  private resize: ResizeObserver;
  private down: { x: number; y: number } | null = null;
  private raycaster = new THREE.Raycaster();
  private hoverId: string | null = null;

  constructor(private host: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    // 手機、小螢幕：解析度與陰影品質降一點，省電也比較順
    const small = Math.min(window.innerWidth, window.innerHeight) < 700;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, small ? 1.5 : 2));
    this.small = small;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    host.appendChild(this.renderer.domElement);
    this.labels = new CSS2DRenderer();
    this.labels.domElement.className = "isl-labels";
    host.appendChild(this.labels.domElement);

    this.camera = new THREE.PerspectiveCamera(32, 1, 0.5, 400);
    this.camera.position.setFromSphericalCoords(78, 0.9, 0.38);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.target.set(0, 0, 1.5);
    this.controls.enableDamping = true;
    this.controls.minDistance = 18;
    this.controls.maxDistance = 110;
    this.controls.minPolarAngle = 0.3;
    this.controls.maxPolarAngle = 1.22;
    this.controls.screenSpacePanning = false;
    this.controls.addEventListener("change", () => {
      const t = this.controls.target; // 不要把島拖出畫面
      const d = Math.hypot(t.x, t.z);
      if (d > 26) t.multiplyScalar(26 / d);
      t.y = 0;
    });

    this.buildWorld();
    this.collectGlow(this.scene);
    this.applyTime(this.minutes());
    this.resize = new ResizeObserver(() => this.fit());
    this.resize.observe(host);
    this.fit();
    const el = this.renderer.domElement;
    el.addEventListener("pointerdown", this.onDown);
    el.addEventListener("pointerup", this.onUp);
    el.addEventListener("pointermove", this.onMove);
    this.idleTimer = window.setInterval(() => this.tickIdle(), 1000);
    this.loop();
  }

  // ---------------------------------------------------------------- 場景

  private buildWorld() {
    const s = this.scene;
    s.background = new THREE.Color(0xb9dcee);
    s.fog = new THREE.Fog(0xb9dcee, 90, 210);
    this.hemi = new THREE.HemisphereLight(0xeef6fa, 0x8a7a68, 1.35);
    s.add(this.hemi);
    const sun = new THREE.DirectionalLight(0xfff1de, 2.7);
    this.sun = sun;
    sun.position.set(24, 38, 16);
    sun.castShadow = true;
    sun.shadow.mapSize.set(this.small ? 1024 : 2048, this.small ? 1024 : 2048);
    const c = sun.shadow.camera;
    c.left = c.bottom = -40;
    c.right = c.top = 40;
    c.far = 120;
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.03;
    s.add(sun);

    const sea = M.sea(260);
    this.seaMat = sea.material as THREE.MeshStandardMaterial;
    s.add(sea);
    s.add(M.island(ISLAND_R));

    // 小路與廣場（同像素版：十字路＋村長廣場）
    const road = 0xd9bf8a;
    s.add(M.flat(new THREE.PlaneGeometry(ISLAND_R * 1.7, 0.75), road, 0, -0.36));
    s.add(M.flat(new THREE.PlaneGeometry(0.75, ISLAND_R * 1.55), road, -0.36, 0));
    s.add(M.flat(new THREE.CircleGeometry(2.1, 24), road, 0, -0.36, 0.2));

    // 裝飾：位置跟像素版一樣（x = 中心格、y = 底邊格）
    let seed = 0;
    for (const d of DECOR) {
      const p = toWorld(d.x, d.y - (d.kind.startsWith("house") ? 1.6 : 0.5));
      let obj: THREE.Object3D | null = null;
      if (d.kind === "tree") obj = M.tree(1.15, seed++);
      else if (d.kind === "pine") obj = M.pine(1.2);
      else if (d.kind === "bush") obj = M.bush();
      else if (d.kind === "rock") obj = M.rock();
      else if (d.kind === "flowers") obj = M.flowers();
      else if (d.kind === "well") obj = M.well();
      else if (d.kind === "sign") obj = M.sign();
      else if (d.kind === "workbench") obj = M.workbench();
      else if (d.kind === "house_red") obj = M.house(0xf3e6cc, 0xd0563f);
      else if (d.kind === "house_blue") obj = M.house(0xeef0f2, 0x3f6fb6);
      if (!obj) continue;
      if (d.kind.startsWith("house")) obj.scale.setScalar(1.25);
      obj.position.set(p.x, 0.18, p.z);
      s.add(obj);
      if (BLOCK_R[d.kind]) this.obstacles.push({ x: p.x, z: p.z, r: BLOCK_R[d.kind] });
    }
    const pondC = toWorld((POND.x0 + POND.x1 + 1) / 2, (POND.y0 + POND.y1 + 1) / 2);
    const pond = M.pond((POND.x1 - POND.x0 + 1) * S, (POND.y1 - POND.y0 + 1) * S);
    pond.position.set(pondC.x, 0, pondC.z);
    s.add(pond);
    this.obstacles.push({ x: pondC.x, z: pondC.z, r: 2.1 });

    // 島上多種一些樹，看起來比較茂密（只種在邊緣，不擋村民的活動範圍）
    for (let i = 0; i < 26; i++) {
      const a = (i / 26) * Math.PI * 2 + (i % 2) * 0.1;
      const r = hexReach(ISLAND_R, a) - 1.4 - (i % 3) * 0.6;
      const t = i % 3 === 0 ? M.pine(0.9 + (i % 4) * 0.1) : M.tree(0.85 + (i % 5) * 0.08, i);
      t.position.set(Math.cos(a) * r, 0.18, Math.sin(a) * r);
      s.add(t);
    }

    // 路燈：廣場四角與小路旁（晚上亮）
    const lampSpots: [number, number][] = [[2.6, 2.0], [-2.9, 2.0], [2.6, -2.8], [-2.9, -2.8], [9, 0.6], [-9, 0.6], [0.6, 8], [0.6, -8.5]];
    for (const [x, z] of lampSpots) {
      const l = M.lamp();
      l.position.set(x, 0.18, z);
      s.add(l);
      this.obstacles.push({ x, z, r: 0.3 });
      const light = new THREE.PointLight(0xffc46b, 0, 9, 1.6);
      light.position.set(x, 2.0, z);
      s.add(light);
      this.lampLights.push(light);
    }
    // 星星：夜晚才看得到
    const starPos: number[] = [];
    for (let i = 0; i < 420; i++) {
      const a = Math.random() * Math.PI * 2;
      const e = 0.12 + Math.random() * 1.2;
      starPos.push(Math.cos(a) * Math.cos(e) * 200, Math.sin(e) * 200, Math.sin(a) * Math.cos(e) * 200);
    }
    const starGeo = new THREE.BufferGeometry();
    starGeo.setAttribute("position", new THREE.Float32BufferAttribute(starPos, 3));
    this.stars = new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xffffff, size: 1.1, transparent: true, opacity: 0, fog: false }));
    s.add(this.stars);

    this.buildSpots();

    // 遠方的山、海上的小船、天上的雲
    const hillColors = [0x7fae6a, 0x8db878, 0x9fbf86, 0x6f9e60];
    for (let i = 0; i < 16; i++) {
      const a = Math.PI * 0.95 + (i / 15) * Math.PI * 1.1;
      const h = M.hill(9 + (i % 4) * 3, 8 + (i % 5) * 3.5, hillColors[i % 4]);
      h.position.set(Math.cos(a) * (115 + (i % 3) * 12), 0, Math.sin(a) * (115 + (i % 3) * 12));
      s.add(h);
    }
    const boatColors = [0xd0563f, 0x3f6fb6, 0xf2c230, 0xffffff];
    for (let i = 0; i < 6; i++) {
      const b = M.boat(boatColors[i % 4]);
      const a = i * 1.1 + 0.4;
      b.position.set(Math.cos(a) * (40 + i * 3), -0.45, Math.sin(a) * (40 + i * 3));
      b.rotation.y = a;
      b.userData.phase = i;
      this.boats.push(b);
      s.add(b);
    }
    for (let i = 0; i < 7; i++) {
      const cl = M.cloud(); // 放在島的外圍、高一點，不會擋到村子
      const a = Math.PI * 0.9 + (i / 6) * Math.PI * 1.2;
      cl.position.set(Math.cos(a) * (70 + (i % 3) * 14), 30 + (i % 3) * 5, Math.sin(a) * (70 + (i % 3) * 14));
      cl.scale.setScalar(1.6 + (i % 3) * 0.5);
      this.clouds.push(cl);
      s.add(cl);
    }
  }

  /** 生活作息的地點：井邊、池塘邊、樹下、告示牌前、花圃、工作台、廣場、房子門口（位置跟著裝飾算） */
  private buildSpots() {
    const per = new Map<THREE.Vector3, number>(); // 每個東西旁邊最多幾個位子
    const add = (kind: SpotKind, pos: THREE.Vector3, face: THREE.Vector3, anchor: THREE.Vector3, max: number) => {
      if ((per.get(anchor) ?? 0) >= max) return;
      if (!insideHex(pos, ISLAND_R, 1.8) || this.blocked(pos.x, pos.z)) return;
      if (this.spots.some((x) => x.pos.distanceTo(pos) < 0.9)) return; // 不要擠在一起
      this.spots.push({ kind, pos: pos.setY(0.18), face, taken: null });
      per.set(anchor, (per.get(anchor) ?? 0) + 1);
    };
    const ring = (c: THREE.Vector3, rx: number, rz: number, n: number, start = 0) =>
      Array.from({ length: n }, (_, i) => {
        const a = start + (i / n) * Math.PI * 2;
        return new THREE.Vector3(c.x + Math.cos(a) * rx, 0.18, c.z + Math.sin(a) * rz);
      });
    for (const d of DECOR) {
      const p = toWorld(d.x, d.y - (d.kind.startsWith("house") ? 1.6 : 0.5));
      const inward = Math.atan2(-p.z, -p.x); // 先試朝島中心那一側
      if (d.kind === "tree" || d.kind === "pine") {
        // 背靠樹幹坐著：面向外面
        for (const q of ring(p, 1.0, 1.0, 6, inward)) add("tree", q, q.clone().multiplyScalar(2).sub(p), p, 1);
      } else if (d.kind === "well") ring(p, 1.5, 1.5, 4, inward).forEach((q) => add("well", q, p.clone(), p, 3));
      else if (d.kind === "sign") [0, -0.8, 0.8].forEach((dx) => add("sign", new THREE.Vector3(p.x + dx, 0, p.z + 1.0), p.clone(), p, 2));
      else if (d.kind === "workbench") ring(p, 1.3, 1.0, 6, inward).forEach((q) => add("workbench", q, p.clone(), p, 2));
      else if (d.kind === "flowers") ring(p, 0.75, 0.75, 4, inward).forEach((q) => add("flowers", q, p.clone(), p, 1));
      else if (d.kind.startsWith("house")) [-0.9, 0, 0.9].forEach((dx) => add("house", new THREE.Vector3(p.x + dx, 0, p.z + 2.4), new THREE.Vector3(p.x + dx, 0, p.z + 6), p, 3));
    }
    const pc = toWorld((POND.x0 + POND.x1 + 1) / 2, (POND.y0 + POND.y1 + 1) / 2);
    const pw = (POND.x1 - POND.x0 + 1) * S;
    const pd = (POND.y1 - POND.y0 + 1) * S;
    ring(pc, pw / 2 + 0.7, Math.max(pd / 2 + 0.7, 2.35), 10).forEach((q) => add("pond", q, pc.clone(), pc, 4));
    const plaza = new THREE.Vector3(0, 0.18, -0.36);
    ring(plaza, 1.6, 1.6, 6, 0.3).forEach((q) => add("plaza", q, plaza.clone(), plaza, 6));
  }

  /** 佔一個地點：挑離家近的（前三近隨機一個）；沒空位回 null。駐村專家用自己小島上的 */
  claimSpot(v: Villager3D, kind: SpotKind): Spot | null {
    let spot: Spot | null = null;
    if (v.islet) spot = kind === "house" ? v.islet.door : v.islet.desk;
    else {
      const free = this.spots.filter((x) => x.kind === kind && (!x.taken || x.taken === v.id));
      free.sort((a, b) => a.pos.distanceTo(v.home) - b.pos.distanceTo(v.home));
      spot = free.length ? free[Math.floor(Math.random() * Math.min(3, free.length))] : null;
      // 晚上門口坐滿了：靠在樹下打瞌睡，再不然在自己家附近
      if (!spot && kind === "house") return this.claimSpot(v, "tree") ?? { kind, pos: v.home.clone(), face: this.cameraGround(), taken: v.id };
    }
    if (spot) spot.taken = v.id;
    return spot;
  }

  releaseSpot(spot: Spot | null) {
    if (spot) spot.taken = null;
  }

  /** 主島上的路線：直走會撞到東西就繞小路（十字路的路口），最多繞兩次 */
  route(from: THREE.Vector3, to: THREE.Vector3): THREE.Vector3[] {
    const clear = (a: THREE.Vector3, b: THREE.Vector3) => {
      const n = Math.ceil(a.distanceTo(b) / 0.4);
      for (let k = 1; k < n; k++) {
        const q = a.clone().lerp(b, k / n);
        if (this.blocked(q.x, q.z)) return false;
      }
      return true;
    };
    if (clear(from, to)) return [to.clone()];
    const nodes = [[-0.36, -0.36], [-7, -0.36], [7, -0.36], [-13, -0.36], [13, -0.36], [-0.36, -6], [-0.36, 6]].map(([x, z]) => new THREE.Vector3(x, 0.18, z));
    let best: THREE.Vector3[] | null = null;
    let bestLen = Infinity;
    const consider = (pts: THREE.Vector3[]) => {
      let len = 0;
      let prev = from;
      for (const q of pts) {
        if (!clear(prev, q)) return;
        len += prev.distanceTo(q);
        prev = q;
      }
      if (len < bestLen) {
        bestLen = len;
        best = pts;
      }
    };
    for (const a of nodes) consider([a, to]);
    if (!best) for (const a of nodes) for (const b of nodes) if (a !== b) consider([a, b, to]);
    return (best ?? [to]).map((q) => q.clone());
  }

  /** 現在幾點（分鐘）：作息跟著天色（工具列選的時段也算） */
  minutesNow(): number {
    return this.minutes();
  }

  /** 生活作息開關：關掉就回到原本的隨便走走 */
  setLife(on: boolean) {
    if (on === this.lifeOn) return;
    this.lifeOn = on;
    this.villagers.forEach((v) => v.restartLife());
  }

  /** 駐村專家的小島：第一次需要時才建 */
  private isletFor(id: string): Islet {
    const existing = this.islets.get(id);
    if (existing) return existing;
    const used = new Set([...this.islets.values()].map((x) => x.center.x.toFixed(1) + x.center.z.toFixed(1)));
    let angle = ISLET_ANGLES[this.islets.size % ISLET_ANGLES.length];
    for (const a of ISLET_ANGLES) {
      const c = new THREE.Vector3(Math.cos(a) * ISLET_DIST, 0, Math.sin(a) * ISLET_DIST);
      if (!used.has(c.x.toFixed(1) + c.z.toFixed(1))) {
        angle = a;
        break;
      }
    }
    const dir = new THREE.Vector3(Math.cos(angle), 0, Math.sin(angle));
    const center = dir.clone().multiplyScalar(ISLET_DIST).setY(0.18);
    const gateIsland = dir.clone().multiplyScalar(hexReach(ISLAND_R, angle) - 0.8).setY(0.18);
    const gateIslet = dir.clone().multiplyScalar(ISLET_DIST - ISLET_R * 0.8).setY(0.18);
    const g = new THREE.Group();
    const isle = M.island(ISLET_R, 1.0, 0x9ccc74);
    isle.position.copy(center).setY(0);
    g.add(isle);
    const br = M.bridge(dir.clone().multiplyScalar(hexReach(ISLAND_R, angle) + 0.3), dir.clone().multiplyScalar(ISLET_DIST - ISLET_R + 0.2));
    br.position.y = 0.13;
    g.add(br);
    const hut = M.house(0xf6efe0, [0x2f7f7a, 0x8a3a6a, 0xc8743a, 0x4a6a9a][this.islets.size % 4]);
    hut.scale.setScalar(0.7);
    const back = dir.clone().multiplyScalar(ISLET_DIST + 1.6);
    hut.position.set(back.x, 0.18, back.z);
    hut.lookAt(0, 0.18, 0);
    g.add(hut);
    const t = M.tree(0.8, this.islets.size);
    const side = new THREE.Vector3(-dir.z, 0, dir.x).multiplyScalar(2.2).add(center);
    t.position.set(side.x, 0.18, side.z);
    g.add(t);
    this.scene.add(g);
    this.collectGlow(g);
    this.applyTime(this.minutes());
    const toMain = center.clone().multiplyScalar(0.5); // 面向主島（也就是鏡頭這邊）
    const door: Spot = { kind: "house", pos: dir.clone().multiplyScalar(ISLET_DIST + 0.3).setY(0.18), face: toMain, taken: null };
    const desk: Spot = { kind: "tree", pos: new THREE.Vector3(-dir.z, 0, dir.x).multiplyScalar(-1.1).add(center).setY(0.18), face: toMain, taken: null };
    const islet = { center, gateIslet, gateIsland, door, desk };
    this.islets.set(id, islet);
    this.isletGroups.set(id, g);
    return islet;
  }

  // ---------------------------------------------------------------- 日夜

  /** 窗戶、路燈的發光材質（晚上亮） */
  private collectGlow(root: THREE.Object3D) {
    root.traverse((o) => {
      if (!(o instanceof THREE.Mesh)) return;
      const m = o.material as THREE.MeshStandardMaterial;
      if ((m.name === "window" || m.name === "lamp") && !this.glowMats.some((g) => g.mat === m)) {
        this.glowMats.push({ mat: m, k: m.name === "lamp" ? 2.4 : 1.4 });
      }
    });
  }

  /** 現在幾點（分鐘）：預設照真實時間；可以指定（預覽用） */
  private minutes(): number {
    if (this.timeOverride !== null) return this.timeOverride;
    const d = new Date();
    return d.getHours() * 60 + d.getMinutes();
  }

  /** 指定時間（分鐘，0～1439）；null = 跟著真實時間 */
  setTime(minutes: number | null) {
    const before = periodOf(this.minutes());
    this.timeOverride = minutes === null ? null : ((minutes % 1440) + 1440) % 1440;
    this.applyTime(this.minutes());
    if (periodOf(this.minutes()) !== before) this.villagers.forEach((v) => v.restartLife()); // 換時段：馬上照新的作息
  }

  private applyTime(min: number) {
    let i = 0;
    while (i < DAY.length - 2 && DAY[i + 1][0] <= min) i++;
    const a = DAY[i];
    const b = DAY[i + 1];
    const t = (min - a[0]) / Math.max(1, b[0] - a[0]);
    const col = (k: number) => new THREE.Color(a[k]).lerp(new THREE.Color(b[k]), t);
    const num = (k: number) => a[k] + (b[k] - a[k]) * t;
    const sky = col(1);
    (this.scene.background as THREE.Color).copy(sky);
    (this.scene.fog as THREE.Fog).color.copy(sky);
    this.sun.color.copy(col(2));
    this.sun.intensity = num(3);
    this.hemi.color.copy(col(4));
    this.hemi.groundColor.copy(col(5));
    this.hemi.intensity = num(6);
    this.night = num(7);
    this.seaMat.color.copy(col(8));
    // 太陽（晚上是月光）繞著島轉：早上從東邊（+x）升起
    const ang = ((min - 360) / 1440) * Math.PI * 2;
    this.sun.position.set(Math.cos(ang) * 36, 18 + Math.abs(Math.sin(ang)) * 22, 18 + Math.sin(ang) * 10);
    for (const g of this.glowMats) g.mat.emissiveIntensity = this.night * g.k;
    for (const l of this.lampLights) l.intensity = this.night * 7;
    (this.stars.material as THREE.PointsMaterial).opacity = Math.max(0, this.night - 0.3) * 1.4;
    this.renderer.toneMappingExposure = 1.05 + this.night * 0.25;
  }

  // ---------------------------------------------------------------- 協作線

  /** 交接時在兩位村民之間畫一條弧線，上面有一顆光點從交件的人流向收件的人 */
  private addFlow(from: Villager3D, to: Villager3D, runId: string | undefined) {
    const a = from.pos.clone().setY(0.35);
    const b = to.pos.clone().setY(0.35);
    const mid = a.clone().lerp(b, 0.5).setY(1.2 + a.distanceTo(b) * 0.22);
    const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
    const color = FLOW_COLORS[this.flows.length % FLOW_COLORS.length];
    const lineMat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.85 });
    const line = new THREE.Mesh(new THREE.TubeGeometry(curve, 40, 0.1, 6, false), lineMat);
    const dotMat = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true });
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.2, 10, 8), dotMat);
    this.scene.add(line, dot);
    line.visible = dot.visible = this.flowsVisible;
    this.flows.push({ curve, line, dot, runId, born: performance.now(), fadeAt: runId ? performance.now() + FLOW_MAX_MS : performance.now() + FLOW_ORPHAN_MS });
  }

  /** 問答結束：那次問答的協作線幾秒後淡出 */
  private endFlows(runId: string | undefined) {
    const at = performance.now() + FLOW_LINGER_MS;
    for (const f of this.flows) if (!runId || f.runId === runId) f.fadeAt = Math.min(f.fadeAt, at);
  }

  private updateFlows(now: number) {
    this.flows = this.flows.filter((f) => {
      const fade = now < f.fadeAt ? 1 : Math.max(0, 1 - (now - f.fadeAt) / 1200);
      const lineMat = f.line.material as THREE.MeshBasicMaterial;
      const dotMat = f.dot.material as THREE.MeshBasicMaterial;
      lineMat.opacity = 0.85 * fade;
      dotMat.opacity = fade;
      f.dot.position.copy(f.curve.getPoint(((now - f.born) / 1600) % 1));
      if (fade > 0) return true;
      f.line.removeFromParent();
      f.dot.removeFromParent();
      f.line.geometry.dispose();
      f.dot.geometry.dispose();
      lineMat.dispose();
      dotMat.dispose();
      return false;
    });
  }

  /** 交接時頭上拿的東西（日誌也用它） */
  carryIcon(id: string): string {
    const v = this.villagers.get(id);
    return v ? this.carryOf(v) : "📜";
  }

  private carryOf(v: Villager3D): string {
    return this.carrying.get(v.id) ?? (v.remote ? "📦" : CARRY_BY_SPRITE[v.sprite] ?? "📜");
  }

  isletAt(p: THREE.Vector3): Islet | null {
    for (const x of this.islets.values()) if (Math.hypot(p.x - x.center.x, p.z - x.center.z) < ISLET_R + 0.5) return x;
    return null;
  }

  private blocked(x: number, z: number): boolean {
    return this.obstacles.some((o) => Math.hypot(x - o.x, z - o.z) < o.r);
  }

  /** 閒晃的目的地：住處附近、在島上、不在障礙物上、路線不穿過障礙物；找不到回 null */
  wanderTarget(home: THREE.Vector3, islet: Islet | null, from: THREE.Vector3): THREE.Vector3 | null {
    for (let i = 0; i < 14; i++) {
      if (islet) {
        const a = Math.random() * Math.PI * 2;
        const r = Math.random() * ISLET_R * 0.45;
        return islet.center.clone().add(new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r));
      }
      const t = home.clone().add(new THREE.Vector3((Math.random() - 0.5) * 7.2, 0, (Math.random() - 0.5) * 7.2));
      if (!insideHex(t, ISLAND_R, 2.2) || this.blocked(t.x, t.z)) continue;
      let ok = true;
      for (let k = 1; k <= 10 && ok; k++) {
        const q = from.clone().lerp(t, k / 10);
        if (this.blocked(q.x, q.z)) ok = false;
      }
      if (ok) return t;
    }
    return null;
  }

  /** 鏡頭正下方的地面點（村民被點到時轉過來面向使用者） */
  cameraGround(): THREE.Vector3 {
    return new THREE.Vector3(this.camera.position.x, 0, this.camera.position.z);
  }

  // ---------------------------------------------------------------- 村民名單

  syncVillagers(list: IslandVillager[], animate = true) {
    const wanted = new Map<string, IslandVillager>([MAYOR, CRAFTSMAN, REVIEWER, SCRIBE, APPRAISER, ...list].map((v) => [v.id, v]));
    const look = (v: IslandVillager) => JSON.stringify([v.sprite, v.name, v.home, !!v.remote]);
    for (const [id, v] of this.villagers) {
      const info = wanted.get(id);
      if (!info || look(info) !== this.looks.get(id)) {
        v.dispose();
        this.villagers.delete(id);
      }
    }
    for (const [id, g] of this.isletGroups) {
      if (!wanted.get(id)?.remote) {
        g.removeFromParent();
        this.isletGroups.delete(id);
        this.islets.delete(id);
      }
    }
    for (const info of wanted.values()) {
      const existing = this.villagers.get(info.id);
      if (existing) {
        existing.setAway(!!info.away);
        existing.lines = info.lines ?? [];
        existing.skill = info.skill ?? "";
        existing.roles = rolesOf(info);
        continue;
      }
      this.looks.set(info.id, look(info));
      const islet = info.remote ? this.isletFor(info.id) : null;
      const home = islet ? islet.center.clone() : this.homeOf(info);
      const v = new Villager3D(this, info.id, info.name, info.sprite ?? "poet", home, islet, !!info.remote);
      v.lines = info.lines ?? [];
      v.skill = info.skill ?? "";
      v.roles = rolesOf(info);
      this.villagers.set(info.id, v);
      this.scene.add(v.root);
      if (animate && !info.away) v.appear();
      v.setAway(!!info.away);
    }
  }

  private homeOf(info: VillagerInfo): THREE.Vector3 {
    let [tx, ty] = info.home ?? [0, 0];
    if (!info.home) {
      let h = 0;
      for (const ch of info.id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
      tx = 3 + (h % (MAP_W - 6));
      ty = 3 + ((h >>> 8) % (MAP_H - 6));
    }
    const p = toWorld(tx, ty);
    for (let r = 0; r < 8; r += 0.6) {
      for (let k = 0; k < 8; k++) {
        const q = p.clone().add(new THREE.Vector3(Math.cos(k) * r, 0, Math.sin(k) * r));
        if (!this.blocked(q.x, q.z) && insideHex(q, ISLAND_R, 2.2)) return q;
      }
    }
    return p;
  }

  say(id: string, text: string) {
    this.villagers.get(id)?.greet(text);
  }

  // ---------------------------------------------------------------- 工具列

  setFlowsVisible(v: boolean) {
    this.flowsVisible = v;
    for (const f of this.flows) f.line.visible = f.dot.visible = v;
  }

  /** 暫停：村民、船、雲、協作線都停住（鏡頭照樣可以轉） */
  setPaused(p: boolean) {
    this.paused = p;
  }

  /** 鳥瞰（從正上方看整座群島）或回到預設的斜角 */
  setBirdView(on: boolean) {
    const pos = on ? new THREE.Vector3(0, 105, 0.01) : new THREE.Vector3().setFromSphericalCoords(78, 0.9, 0.38);
    const target = on ? new THREE.Vector3(0, 0, 0) : new THREE.Vector3(0, 0, 1.5);
    this.controls.minPolarAngle = on ? 0 : 0.3;
    this.flyTo(pos, target);
  }

  /** 鏡頭飛到某位村民（日誌點一下） */
  focus(id: string) {
    const v = this.villagers.get(id);
    if (!v) return;
    const target = v.pos.clone().setY(0);
    const offset = new THREE.Vector3().setFromSphericalCoords(30, 0.85, this.controls.getAzimuthalAngle());
    this.controls.minPolarAngle = 0.3;
    this.flyTo(target.clone().add(offset), target);
    v.greetPulse();
  }

  private flyTo(pos: THREE.Vector3, target: THREE.Vector3) {
    this.camAnim = { fromPos: this.camera.position.clone(), toPos: pos, fromT: this.controls.target.clone(), toT: target, t: 0 };
  }

  setNamesVisible(v: boolean) {
    this.villagers.forEach((x) => x.setNameVisible(v));
  }

  // ---------------------------------------------------------------- 事件（同像素版）

  handleEvent(e: VillageEvent) {
    const v = e.agent ? this.villagers.get(e.agent) : undefined;
    if (e.type === "agent.finished" && e.agent) {
      const icon = carryFromAttachments(e.attachments);
      if (icon) this.carrying.set(e.agent, icon);
      else this.carrying.delete(e.agent);
    }
    if (["run.finished", "run.failed", "run.cancelled"].includes(e.type)) this.endFlows(e.run_id);
    switch (e.type) {
      case "run.started":
        v?.startWork("thinking");
        break;
      case "plan.created":
        v?.finish(true);
        break;
      case "run.failed":
        v?.finish(false);
        break;
      case "agent.started":
      case "workshop.started":
      case "review.started":
      case "memory.started":
      case "appraise.started":
        v?.startWork();
        break;
      case "agent.finished":
      case "workshop.finished":
      case "memory.finished":
        v?.finish(true);
        break;
      case "agent.error":
      case "workshop.error":
        v?.finish(false);
        break;
      case "review.finished":
        v?.finish(e.passed !== false);
        break;
      case "appraise.finished":
        v?.finish(e.ok !== false);
        break;
      case "run.cancelled":
        this.villagers.forEach((x) => x.busy && x.finish(false));
        break;
      case "agent.handoff": {
        const from = this.villagers.get(e.from_agent ?? "");
        const to = this.villagers.get(e.to_agent ?? "");
        if (from && to) {
          from.handoffTo(to, this.carryOf(from));
          this.addFlow(from, to, e.run_id);
        }
        break;
      }
    }
  }

  private tickIdle() {
    if (this.paused) return;
    const now = performance.now();
    const all = [...this.villagers.values()];
    if (now >= this.nextAmbient) {
      this.nextAmbient = now + between(AMBIENT_MS);
      const free = all.filter((v) => v.idle && !v.speaking && !v.sleeping);
      if (free.length && all.filter((v) => v.speaking).length < MAX_SPEAKING) {
        const v = pick(free);
        const line = v.ambientLine();
        if (line) v.say(line);
      }
    }
    if (now >= this.nextChat) {
      this.nextChat = now + between(CHAT_MS);
      const free = all.filter((v) => v.chatty && !v.speaking);
      const a = free.length > 1 ? pick(free) : undefined;
      const near = a ? free.filter((v) => v !== a && v.pos.distanceTo(a.pos) < CHAT_RANGE) : [];
      if (a && near.length) {
        const b = pick(near);
        a.chatWith(b, chatScript(b));
      }
    }
  }

  // ---------------------------------------------------------------- 點村民

  private pickVillager(ev: PointerEvent): string | null {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    const hits = this.raycaster.intersectObjects([...this.villagers.values()].map((v) => v.root), true);
    for (const h of hits) {
      let o: THREE.Object3D | null = h.object;
      while (o && !o.userData.villagerId) o = o.parent;
      if (o) return o.userData.villagerId as string;
    }
    return null;
  }

  private onDown = (ev: PointerEvent) => {
    this.down = { x: ev.clientX, y: ev.clientY };
  };

  private onUp = (ev: PointerEvent) => {
    const d = this.down;
    this.down = null;
    if (!d || Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > 5) return; // 拖曳是在轉鏡頭，不算點擊
    const id = this.pickVillager(ev);
    if (id) this.onVillagerClick?.(id);
  };

  private onMove = (ev: PointerEvent) => {
    if (this.down) return;
    const id = this.pickVillager(ev);
    if (id !== this.hoverId) {
      this.hoverId = id;
      this.renderer.domElement.style.cursor = id ? "pointer" : "";
    }
  };

  // ---------------------------------------------------------------- 迴圈

  private fit() {
    const w = Math.max(1, this.host.clientWidth);
    const h = Math.max(1, this.host.clientHeight);
    this.renderer.setSize(w, h);
    this.labels.setSize(w, h);
    this.camera.aspect = w / h;
    // 直式畫面（手機）拉遠一點，整座島才放得下
    this.camera.fov = w / h < 1 ? 46 : 32;
    this.camera.updateProjectionMatrix();
  }

  private loop = () => {
    this.frame = requestAnimationFrame(this.loop);
    const now = performance.now();
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    if (!this.paused) {
      this.villagers.forEach((v) => v.update(dt, now));
      for (const b of this.boats) {
        const ph = b.userData.phase as number;
        b.position.y = -0.45 + Math.sin(now / 900 + ph) * 0.06;
        b.rotation.z = Math.sin(now / 1100 + ph) * 0.05;
      }
      for (const c of this.clouds) {
        c.position.x += dt * 0.5;
        if (c.position.x > 110) c.position.x = -110;
      }
      this.updateFlows(now);
    }
    if (this.camAnim) {
      const a = this.camAnim;
      a.t = Math.min(1, a.t + dt / 0.9);
      const e = a.t < 0.5 ? 2 * a.t * a.t : 1 - Math.pow(-2 * a.t + 2, 2) / 2;
      this.camera.position.lerpVectors(a.fromPos, a.toPos, e);
      this.controls.target.lerpVectors(a.fromT, a.toT, e);
      if (a.t >= 1) this.camAnim = null;
    }
    if (now >= this.timeTimer) {
      this.timeTimer = now + 20_000; // 天色每 20 秒跟著真實時間更新一次
      this.applyTime(this.minutes());
    }
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
  };

  dispose() {
    cancelAnimationFrame(this.frame);
    window.clearInterval(this.idleTimer);
    this.resize.disconnect();
    const el = this.renderer.domElement;
    el.removeEventListener("pointerdown", this.onDown);
    el.removeEventListener("pointerup", this.onUp);
    el.removeEventListener("pointermove", this.onMove);
    this.villagers.forEach((v) => v.dispose());
    this.controls.dispose();
    this.renderer.dispose();
    el.remove();
    this.labels.domElement.remove();
  }
}
