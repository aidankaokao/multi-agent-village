/**
 * 3D 村子（Three.js）：和像素版（village/VillageScene.ts）吃一樣的村民名單與事件，畫法不同。
 * 場景（地形、裝飾、駐村專家住的地方）由 Terrain 提供——島嶼（island/IslandTerrain.ts），之後是鐵道小鎮；
 * 這裡是場景共用的部分：
 *
 * - 名牌、冒泡、狀態用 HTML（CSS2DRenderer），文字清楚、不受 3D 縮放影響
 * - 村民的行為（冒一句、兩人聊天、派工、交接、外出中）照像素版；閒著時照生活作息（village/life.ts）
 *   走去井邊、池塘、樹下做事，派工時立刻中斷，做完再回去接著做
 * - 日夜（依真實時間）、交接時頭上拿的東西、協作線、鏡頭（鳥瞰、飛到村民）
 */
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import type { VillageEvent } from "../events";
import { APPRAISER, CRAFTSMAN, MAP_H, MAP_W, MAYOR, REVIEWER, SCRIBE, type VillagerInfo } from "../village/VillageScene";
import { periodOf, rolesOf, type SpotKind } from "../village/life";
import {
  CARRY_BY_SPRITE,
  DAY,
  FLOW_COLORS,
  FLOW_LINGER_MS,
  FLOW_MAX_MS,
  FLOW_ORPHAN_MS,
  between,
  carryFromAttachments,
  pick,
  toWorld,
  type BuildCtx,
  type Flow,
  type Obstacle,
  type Outpost,
  type Spot,
  type Terrain,
} from "./common";
import * as M from "./models";
import { Villager3D } from "./Villager3D";

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

function chatScript(b: Villager3D): [string, string, string?] {
  if (b.skill && Math.random() < 0.6) {
    const ask = `${b.name}，你會${b.skill}對吧？`;
    return [ask.length > 16 ? `你會${b.skill}對吧？` : ask, "對啊，交給我！", "下次找你幫忙"];
  }
  return pick(GREETINGS);
}

// ---------------------------------------------------------------- 世界

export interface WorldVillager extends VillagerInfo {
  remote?: boolean;
}

export class World3D {
  onVillagerClick?: (id: string) => void;
  /** 場景（交通工具）想寫進村莊日誌的話 */
  onLog?: (icon: string, text: string, agent?: string, kind?: "info" | "ok" | "bad") => void;
  private renderer: THREE.WebGLRenderer;
  private labels: CSS2DRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private controls: OrbitControls;
  private villagers = new Map<string, Villager3D>();
  private looks = new Map<string, string>();
  private outposts = new Map<string, { outpost: Outpost; group: THREE.Group; slot: number }>(); // 駐村專家 id → 住的地方
  private obstacles: Obstacle[] = [];
  private spots: Spot[] = []; // 村子裡做事的地點
  private ctx: BuildCtx;
  lifeOn = true;
  // 日夜
  private hemi!: THREE.HemisphereLight;
  private sun!: THREE.DirectionalLight;
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
  private frame = 0;
  private last = performance.now();
  private nextAmbient = performance.now() + between(AMBIENT_MS);
  private nextChat = performance.now() + between(CHAT_MS);
  private idleTimer: number;
  private resize: ResizeObserver;
  private down: { x: number; y: number } | null = null;
  private raycaster = new THREE.Raycaster();
  private hoverId: string | null = null;

  constructor(
    private host: HTMLElement,
    private terrain: Terrain,
  ) {
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

    const view = terrain.view;
    this.camera = new THREE.PerspectiveCamera(32, 1, 0.5, 400);
    this.camera.position.setFromSphericalCoords(view.dist, view.polar, view.azimuth);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.target.copy(view.target);
    this.controls.enableDamping = true;
    this.controls.minDistance = view.minDist;
    this.controls.maxDistance = view.maxDist;
    this.controls.minPolarAngle = 0.3;
    this.controls.maxPolarAngle = 1.22;
    this.controls.screenSpacePanning = false;
    this.controls.addEventListener("change", () => {
      const t = this.controls.target; // 不要把村子拖出畫面
      const d = Math.hypot(t.x, t.z);
      if (d > view.panLimit) t.multiplyScalar(view.panLimit / d);
      t.y = 0;
    });

    this.ctx = {
      scene: this.scene,
      small,
      addObstacle: (x, z, r) => this.obstacles.push({ x, z, r }),
      blocked: (x, z) => this.blocked(x, z),
      addLamp: (x, z) => this.addLamp(x, z),
    };
    this.buildWorld();
    const world = this; // eslint-disable-line @typescript-eslint/no-this-alias
    this.terrain.courier?.attach({
      log: (icon, text, agent, kind) => this.onLog?.(icon, text, agent, kind),
      get flowsVisible() {
        return world.flowsVisible;
      },
    });
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
    s.fog = new THREE.Fog(0xb9dcee, ...this.terrain.view.fog);
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

    this.terrain.build(this.ctx);

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

    this.spots = this.terrain.spots(this.ctx);
  }

  /** 路燈（晚上亮）：燈柱、點光源、障礙物 */
  private addLamp(x: number, z: number) {
    const l = M.lamp();
    l.position.set(x, 0.18, z);
    this.scene.add(l);
    this.obstacles.push({ x, z, r: 0.3 });
    const light = new THREE.PointLight(0xffc46b, 0, 9, 1.6);
    light.position.set(x, 2.0, z);
    this.scene.add(light);
    this.lampLights.push(light);
  }

  /** 佔一個地點：挑離家近的（前三近隨機一個）；沒空位回 null。駐村專家用自己住的地方的 */
  claimSpot(v: Villager3D, kind: SpotKind): Spot | null {
    let spot: Spot | null = null;
    if (v.outpost) spot = kind === "house" ? v.outpost.door : v.outpost.desk;
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

  /** 村子裡的路線：直走會撞到東西就繞小路（場景給的路口），最多繞兩次 */
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
    const nodes = this.terrain.routeNodes;
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

  /** 駐村專家住的地方：第一次需要時才蓋（用最前面還空著的位置） */
  private outpostFor(info: WorldVillager): Outpost {
    const existing = this.outposts.get(info.id);
    if (existing) return existing.outpost;
    const used = new Set([...this.outposts.values()].map((x) => x.slot));
    let slot = 0;
    while (used.has(slot)) slot++;
    const { outpost, group } = this.terrain.outpost(slot, this.ctx, { id: info.id, name: info.name, skill: info.skill });
    this.scene.add(group);
    this.collectGlow(group);
    this.applyTime(this.minutes());
    this.outposts.set(info.id, { outpost, group, slot });
    return outpost;
  }

  private removeOutpost(id: string) {
    const o = this.outposts.get(id);
    if (!o) return;
    o.group.removeFromParent();
    const mats = new Set<THREE.Material>();
    o.group.traverse((x) => {
      if (x instanceof THREE.Mesh) mats.add(x.material as THREE.Material);
      if (x instanceof CSS2DObject) x.element.remove(); // HTML 標籤不會跟著拿掉
    });
    this.glowMats = this.glowMats.filter((g) => !mats.has(g.mat));
    this.outposts.delete(id);
  }

  /** 駐村專家外出中：那裡的窗戶、燈不亮（場景可以再加看板） */
  private setOutpostAway(id: string, away: boolean) {
    const o = this.outposts.get(id);
    if (!o || o.group.userData.away === away) return;
    o.group.userData.away = away;
    o.group.traverse((x) => {
      const m = x instanceof THREE.Mesh ? (x.material as THREE.Material) : null;
      if (m && (m.name === "window" || m.name === "lamp")) m.userData.off = away;
    });
    this.terrain.outpostAway?.(o.group, away);
    this.applyTime(this.minutes());
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
    this.terrain.tint(col(8), this.night);
    // 太陽（晚上是月光）繞著島轉：早上從東邊（+x）升起
    const ang = ((min - 360) / 1440) * Math.PI * 2;
    this.sun.position.set(Math.cos(ang) * 36, 18 + Math.abs(Math.sin(ang)) * 22, 18 + Math.sin(ang) * 10);
    for (const g of this.glowMats) g.mat.emissiveIntensity = g.mat.userData.off ? 0 : this.night * g.k;
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

  /** 站在哪位駐村專家住的地方（在村子裡回 null） */
  outpostAt(p: THREE.Vector3): Outpost | null {
    for (const { outpost: o } of this.outposts.values()) if (Math.hypot(p.x - o.center.x, p.z - o.center.z) < o.radius) return o;
    return null;
  }

  private blocked(x: number, z: number): boolean {
    return this.obstacles.some((o) => Math.hypot(x - o.x, z - o.z) < o.r);
  }

  /** 閒晃的目的地：住處附近、在村子範圍內、不在障礙物上、路線不穿過障礙物；找不到回 null */
  wanderTarget(home: THREE.Vector3, outpost: Outpost | null, from: THREE.Vector3): THREE.Vector3 | null {
    for (let i = 0; i < 14; i++) {
      if (outpost) {
        const a = Math.random() * Math.PI * 2;
        const r = Math.random() * outpost.radius * 0.4;
        return outpost.center.clone().add(new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r));
      }
      const t = home.clone().add(new THREE.Vector3((Math.random() - 0.5) * 7.2, 0, (Math.random() - 0.5) * 7.2));
      if (!this.terrain.inside(t, 2.2) || this.blocked(t.x, t.z)) continue;
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

  syncVillagers(list: WorldVillager[], animate = true) {
    const wanted = new Map<string, WorldVillager>([MAYOR, CRAFTSMAN, REVIEWER, SCRIBE, APPRAISER, ...list].map((v) => [v.id, v]));
    const look = (v: WorldVillager) => JSON.stringify([v.sprite, v.name, v.home, !!v.remote]);
    for (const [id, v] of this.villagers) {
      const info = wanted.get(id);
      if (!info || look(info) !== this.looks.get(id)) {
        v.dispose();
        this.villagers.delete(id);
      }
    }
    for (const id of [...this.outposts.keys()]) {
      if (!wanted.get(id)?.remote || !this.villagers.has(id)) this.removeOutpost(id); // 改了名字：重蓋（樣式可能不同）
    }
    for (const info of wanted.values()) {
      const existing = this.villagers.get(info.id);
      if (existing) {
        existing.setAway(!!info.away);
        this.setOutpostAway(info.id, !!info.away);
        existing.lines = info.lines ?? [];
        existing.skill = info.skill ?? "";
        existing.roles = rolesOf(info);
        continue;
      }
      this.looks.set(info.id, look(info));
      const outpost = info.remote ? this.outpostFor(info) : null;
      const home = outpost ? outpost.center.clone() : this.homeOf(info);
      const v = new Villager3D(this, info.id, info.name, info.sprite ?? "poet", home, outpost, !!info.remote);
      v.lines = info.lines ?? [];
      v.skill = info.skill ?? "";
      v.roles = rolesOf(info);
      this.villagers.set(info.id, v);
      this.scene.add(v.root);
      if (animate && !info.away) v.appear();
      v.setAway(!!info.away);
      this.setOutpostAway(info.id, !!info.away);
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
        if (!this.blocked(q.x, q.z) && this.terrain.inside(q, 2.2)) return q;
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
    this.terrain.courier?.setFlowsVisible(v);
  }

  /** 暫停：村民、船、雲、協作線都停住（鏡頭照樣可以轉） */
  setPaused(p: boolean) {
    this.paused = p;
  }

  /** 鳥瞰（從正上方看整個場景）或回到預設的斜角 */
  setBirdView(on: boolean) {
    const v = this.terrain.view;
    const pos = on ? new THREE.Vector3(0, v.birdHeight, 0.01) : new THREE.Vector3().setFromSphericalCoords(v.dist, v.polar, v.azimuth);
    const target = on ? new THREE.Vector3(0, 0, 0) : v.target.clone();
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
    const courier = this.terrain.courier;
    if (["run.finished", "run.failed", "run.cancelled"].includes(e.type)) {
      this.endFlows(e.run_id);
      courier?.runEnded(e.run_id);
    }
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
        v?.startWork();
        if (v) courier?.started(v, e.run_id);
        break;
      case "workshop.started":
      case "review.started":
      case "memory.started":
      case "appraise.started":
        v?.startWork();
        break;
      case "agent.finished":
        v?.finish(true);
        if (v) courier?.finished(v, this.carryOf(v), e.run_id);
        break;
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
          if (courier?.handoff(from, to, this.carryOf(from), e.run_id)) break; // 交通工具送（例如經過中轉站、搭火車）
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
      this.terrain.update(dt, now);
      this.terrain.courier?.update(dt, now);
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
    this.terrain.courier?.dispose();
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
