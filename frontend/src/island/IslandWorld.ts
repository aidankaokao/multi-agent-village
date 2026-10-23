/**
 * 島嶼版的村子（Three.js）：和像素版（village/VillageScene.ts）吃一樣的村民名單與事件，畫法不同。
 *
 * - 主島：像素地圖的 30×20 格對應到一座圓角六邊形的島；裝飾、池塘、小路的位置和像素版一樣
 * - 駐村專家住在外圍的小島，用木棧橋連到主島；交接時沿著橋走過來
 * - 名牌、冒泡、狀態用 HTML（CSS2DRenderer），文字清楚、不受 3D 縮放影響
 * - 村民的行為（閒晃、冒一句、兩人聊天、派工、交接、外出中）照像素版
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import type { VillageEvent } from "../events";
import { APPRAISER, CRAFTSMAN, DECOR, MAP_H, MAP_W, MAYOR, POND, REVIEWER, SCRIBE, type VillagerInfo } from "../village/VillageScene";
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
  private path: THREE.Vector3[] = [];
  private onArrive?: () => void;
  private walkT = 0;
  private wanderAt = 0;
  private timers: number[] = [];
  private speechTimer?: number;
  private statusTimer?: number;
  private appearT = -1;
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
    sprite: string,
    public home: THREE.Vector3,
    public islet: Islet | null,
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
    this.nameEl = document.createElement("div");
    this.nameEl.className = "isl-name";
    this.nameEl.textContent = name;
    el.append(this.speechEl, this.statusEl, this.nameEl);
    this.tag = new CSS2DObject(el);
    this.tag.center.set(0.5, 1);
    this.tag.position.set(0, TAG_Y, 0);
    this.root.add(this.tag);
    this.wanderAt = performance.now() + 300 + Math.random() * 2200;
  }

  get idle(): boolean {
    return !this.busy && !this.chatting && !this.away;
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
    const swing = this.path.length ? Math.sin(this.walkT) * 0.5 : 0;
    this.model.legs[0].rotation.x = swing;
    this.model.legs[1].rotation.x = -swing;
    this.model.arms[0].rotation.x = -swing * 0.8;
    this.model.arms[1].rotation.x = swing * 0.8;
    this.model.root.children[0].position.y = this.path.length ? Math.abs(Math.sin(this.walkT)) * 0.06 : Math.sin(now / 600 + p.x) * 0.012;
    if (this.appearT >= 0) {
      this.appearT = Math.min(1, this.appearT + dt * 1.6);
      const t = this.appearT;
      this.root.scale.setScalar(VILLAGER_SCALE * (t < 1 ? 1.15 * (1 - Math.pow(1 - t, 3)) : 1));
      if (t >= 1) this.appearT = -1;
    }
    if (!this.busy && !this.chatting && !this.path.length && now >= this.wanderAt) this.wander(now);
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
    if (!this.busy && !this.chatting) {
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

  /** 走到另一位村民旁邊交接，再回家 */
  handoffTo(other: Villager3D) {
    this.endChat();
    other.endChat();
    this.busy = true;
    const dir = new THREE.Vector3(this.pos.x - other.pos.x, 0, this.pos.z - other.pos.z);
    if (dir.lengthSq() < 0.01) dir.set(1, 0, 0);
    const spot = other.pos.clone().add(dir.setLength(1.1));
    this.walkTo(spot, () => {
      this.faceTo(other.pos);
      other.faceTo(this.pos);
      this.timers.push(window.setTimeout(() => {
        this.busy = false;
        this.walkTo(this.world.wanderTarget(this.home, this.islet, this.home) ?? this.home.clone(), undefined, this.islet);
      }, 800));
    }, other.islet);
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
    if (away) this.stop();
  }

  appear() {
    this.appearT = 0;
    this.root.scale.setScalar(0.01);
    this.showBubble("done", 2000);
  }

  dispose() {
    this.timers.forEach((t) => window.clearTimeout(t));
    window.clearTimeout(this.speechTimer);
    window.clearTimeout(this.statusTimer);
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
  private boats: THREE.Group[] = [];
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
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
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
    s.add(new THREE.HemisphereLight(0xeef6fa, 0x8a7a68, 1.35));
    const sun = new THREE.DirectionalLight(0xfff1de, 2.7);
    sun.position.set(24, 38, 16);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    const c = sun.shadow.camera;
    c.left = c.bottom = -40;
    c.right = c.top = 40;
    c.far = 120;
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.03;
    s.add(sun);

    s.add(M.sea(260));
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
    const islet = { center, gateIslet, gateIsland };
    this.islets.set(id, islet);
    this.isletGroups.set(id, g);
    return islet;
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
        continue;
      }
      this.looks.set(info.id, look(info));
      const islet = info.remote ? this.isletFor(info.id) : null;
      const home = islet ? islet.center.clone() : this.homeOf(info);
      const v = new Villager3D(this, info.id, info.name, info.sprite ?? "poet", home, islet);
      v.lines = info.lines ?? [];
      v.skill = info.skill ?? "";
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

  setNamesVisible(v: boolean) {
    this.villagers.forEach((x) => x.setNameVisible(v));
  }

  // ---------------------------------------------------------------- 事件（同像素版）

  handleEvent(e: VillageEvent) {
    const v = e.agent ? this.villagers.get(e.agent) : undefined;
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
        if (from && to) from.handoffTo(to);
        break;
      }
    }
  }

  private tickIdle() {
    const now = performance.now();
    const all = [...this.villagers.values()];
    if (now >= this.nextAmbient) {
      this.nextAmbient = now + between(AMBIENT_MS);
      const free = all.filter((v) => v.idle && !v.speaking && v.lines.length);
      if (free.length && all.filter((v) => v.speaking).length < MAX_SPEAKING) {
        const v = pick(free);
        v.say(pick(v.lines));
      }
    }
    if (now >= this.nextChat) {
      this.nextChat = now + between(CHAT_MS);
      const free = all.filter((v) => v.idle && !v.speaking);
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
