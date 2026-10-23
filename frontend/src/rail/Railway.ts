/**
 * 鐵道小鎮的交通：火車＋中轉站（倉庫）。所有和駐村專家之間的交接都經過中轉站：
 *
 * - 誰產生成果，誰就把它放進中轉站（貨架上一個貼號碼的包裹）：
 *   村子裡的村民自己走去放；駐村專家做完，火車開去它的小站載回來放。
 * - 誰需要，誰去中轉站拿：村子裡的村民自己走去拿；駐村專家由火車從中轉站載過去。
 * - 駐村專家開工時沒有人交東西給它（第一步）：火車送「村長的指示」📨 過去。
 * - 村民之間（都在村子裡）照舊：走過去交給對方（不經過這裡）。
 *
 * 火車一次做一件事（工作排隊，`chain`）；開動時那段鐵道亮起來（取代弧形協作線），到站後幾秒淡出。
 * 這些只是動畫：真正的檔案傳遞在後端（core/depot.py），這裡的號碼是畫面上的順序。
 */
import * as THREE from "three";
import { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import type { Courier, CourierHost, Outpost } from "../world3d/common";
import type { Villager3D } from "../world3d/Villager3D";

const VMAX = 0.4; // 弧度 / 秒（半徑 24：約每秒 10 單位，繞半圈約 8 秒）
const GLOW_LINGER_MS = 4000;
const CLEAR_AFTER_MS = 6000; // 火車把這次問答的東西都送完後，中轉站的包裹再留幾秒
const PLACE_FALLBACK_MS = 25_000; // 村民走去放東西，最慢這麼久後當作放好了

interface Parcel {
  n: number;
  icon: string;
  from: string; // 誰的成果（名字）
  placed: Promise<void>;
  done: () => void;
  isPlaced: boolean;
  slot: number | null;
  mesh: THREE.Group | null;
  label: CSS2DObject | null;
}

interface Glow {
  mesh: THREE.Mesh;
  fadeAt: number;
}

function label(text: string, cls: string): CSS2DObject {
  const el = document.createElement("div");
  el.className = cls;
  el.textContent = text;
  return new CSS2DObject(el);
}

function box(): THREE.Group {
  const g = new THREE.Group();
  const m = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.26, 0.3), new THREE.MeshStandardMaterial({ color: 0xd8a860, flatShading: true }));
  m.castShadow = true;
  g.add(m);
  const tape = new THREE.Mesh(new THREE.BoxGeometry(0.36, 0.04, 0.08), new THREE.MeshStandardMaterial({ color: 0xc8503a }));
  tape.position.y = 0.12;
  g.add(tape);
  return g;
}

export class Railway implements Courier {
  private host: CourierHost | null = null;
  private theta: number;
  private remaining = 0;
  private dir = 1;
  private arrive: (() => void) | null = null;
  private chain: Promise<void> = Promise.resolve();
  private parcels: Parcel[] = [];
  private latest = new Map<string, Parcel>(); // 駐村專家 id → 它最近的成果
  private expecting = new Set<string>(); // 等著收東西的駐村專家（有人交接給它）
  private seq = 0;
  private glows: Glow[] = [];
  private glowsVisible = true;
  private cargo: CSS2DObject;
  private clearTimer?: number;
  private disposed = false;

  constructor(
    private scene: THREE.Scene,
    private train: THREE.Group,
    private depot: THREE.Group,
    private stopOf: (o: Outpost) => number | undefined,
    private mainStop: number,
    private radius: number,
  ) {
    this.theta = mainStop;
    this.cargo = label("", "isl-cargo");
    this.cargo.position.set(-0.8, 2.0, 0);
    this.cargo.visible = false;
    train.add(this.cargo);
    this.placeTrain();
  }

  attach(host: CourierHost) {
    this.host = host;
    this.glowsVisible = host.flowsVisible;
  }

  private log(icon: string, text: string, agent?: string, kind?: "info" | "ok" | "bad") {
    this.host?.log(icon, text, agent, kind);
  }

  // ---------------------------------------------------------------- 交接（World3D 呼叫）

  handoff(from: Villager3D, to: Villager3D, icon: string): boolean {
    if (!from.outpost && !to.outpost) return false; // 都在村子裡：走過去交
    if (to.outpost) this.expecting.add(to.id);
    let p: Parcel;
    if (from.outpost) {
      p = this.latest.get(from.id) ?? this.collect(from, icon); // 通常做完時火車已經去載了
    } else {
      p = this.newParcel(from.name, icon);
      const [door, face] = this.depotDoor();
      from.errand(door, face, () => {
        this.place(p);
        this.log("📦", `${from.name}把 #${p.n} ${p.icon} 放進中轉站`, from.id);
      }, icon);
      window.setTimeout(() => this.place(p), PLACE_FALLBACK_MS);
    }
    if (to.outpost) this.deliver(p, to);
    else {
      void p.placed.then(() => {
        if (this.disposed) return;
        const [door, face] = this.depotDoor();
        to.errand(door, face, () => {
          this.take(p);
          this.log("🙋", `${to.name}到中轉站取走 #${p.n} ${p.icon}`, to.id);
        }, null, p.icon);
      });
    }
    return true;
  }

  started(v: Villager3D) {
    if (!v.outpost) return;
    if (this.expecting.delete(v.id)) return; // 有人交東西給它：火車已經在送了
    const stop = this.stopOf(v.outpost);
    if (stop === undefined) return;
    this.job(async () => {
      await this.go(this.mainStop);
      this.load("📨");
      this.log("🚂", `列車把村長的指示送往${v.name}站`, v.id);
      await this.go(stop);
      this.unload();
      v.carry("📨", 2200);
    });
  }

  finished(v: Villager3D, icon: string) {
    if (v.outpost) this.latest.set(v.id, this.collect(v, icon));
  }

  /** 問答結束：排在火車最後一件工作（還在路上的東西先送完），再等幾秒清空中轉站、回主車站 */
  runEnded() {
    this.job(async () => {
      await new Promise<void>((r) => (this.clearTimer = window.setTimeout(r, CLEAR_AFTER_MS)));
      for (const p of [...this.parcels]) this.take(p);
      this.latest.clear();
      this.expecting.clear();
      await this.go(this.mainStop);
    });
  }

  setFlowsVisible(v: boolean) {
    this.glowsVisible = v;
    for (const g of this.glows) g.mesh.visible = v;
  }

  // ---------------------------------------------------------------- 工作

  /** 火車去駐村專家的小站載成果回中轉站 */
  private collect(v: Villager3D, icon: string): Parcel {
    const p = this.newParcel(v.name, icon);
    const stop = v.outpost ? this.stopOf(v.outpost) : undefined;
    this.job(async () => {
      if (stop !== undefined) {
        this.log("🚂", `列車開往${v.name}站載成果`, v.id);
        await this.go(stop);
        v.carry(null);
        this.load(icon);
        await this.go(this.mainStop);
        this.unload();
      }
      this.place(p);
      this.log("📦", `#${p.n} ${p.icon}（${v.name}的成果）送進中轉站`);
    });
    return p;
  }

  /** 火車從中轉站把包裹載到駐村專家的小站 */
  private deliver(p: Parcel, to: Villager3D) {
    const stop = to.outpost ? this.stopOf(to.outpost) : undefined;
    if (stop === undefined) return;
    this.job(async () => {
      await p.placed;
      await this.go(this.mainStop);
      this.take(p);
      this.load(p.icon);
      this.log("🚂", `列車載著 #${p.n} ${p.icon} 開往${to.name}站`, to.id);
      await this.go(stop);
      this.unload();
      to.carry(p.icon, 2200);
      this.log("📮", `${to.name}收到 #${p.n} ${p.icon}`, to.id, "ok");
    });
  }

  private job(task: () => Promise<void>) {
    this.chain = this.chain.then(() => (this.disposed ? undefined : task())).catch(() => undefined);
  }

  // ---------------------------------------------------------------- 中轉站的包裹

  private newParcel(from: string, icon: string): Parcel {
    let done = () => {};
    const placed = new Promise<void>((r) => (done = r));
    return { n: ++this.seq, icon, from, placed, done, isPlaced: false, slot: null, mesh: null, label: null };
  }

  private slots(): THREE.Vector3[] {
    return (this.depot.userData.slots as THREE.Vector3[]) ?? [];
  }

  /** 放上貨架（滿了就把最舊的收掉） */
  private place(p: Parcel) {
    if (p.isPlaced || this.disposed) return;
    p.isPlaced = true;
    const slots = this.slots();
    const used = new Set(this.parcels.map((x) => x.slot));
    let slot = slots.findIndex((_, i) => !used.has(i));
    if (slot < 0 && this.parcels.length) {
      const old = this.parcels[0];
      this.take(old);
      slot = old.slot ?? 0;
    }
    p.slot = Math.max(0, slot);
    p.mesh = box();
    p.mesh.position.copy(slots[p.slot] ?? new THREE.Vector3());
    p.label = label(`#${p.n} ${p.icon}`, "isl-parcel");
    p.label.position.set(0, 0.32, 0);
    p.mesh.add(p.label);
    this.depot.add(p.mesh);
    this.parcels.push(p);
    p.done();
  }

  /** 從貨架拿走 */
  private take(p: Parcel) {
    this.parcels = this.parcels.filter((x) => x !== p);
    if (!p.mesh) return;
    p.label?.element.remove();
    p.mesh.removeFromParent();
    p.mesh.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        o.geometry.dispose();
        (o.material as THREE.Material).dispose();
      }
    });
    p.mesh = null;
    p.label = null;
  }

  /** 倉庫門口（村民放 / 拿東西站的地方）與要面向哪裡 */
  private depotDoor(): [THREE.Vector3, THREE.Vector3] {
    this.depot.updateWorldMatrix(true, false);
    const door = this.depot.localToWorld(new THREE.Vector3(0, 0, 1.5)).setY(0.18);
    const inside = this.depot.localToWorld(new THREE.Vector3(0, 0, -0.5)).setY(0.18);
    return [door, inside];
  }

  // ---------------------------------------------------------------- 火車

  private load(icon: string) {
    (this.cargo.element as HTMLDivElement).textContent = icon;
    this.cargo.visible = true;
  }

  private unload() {
    this.cargo.visible = false;
  }

  /** 開到某個角度（走比較近的方向）；到了才 resolve */
  private go(angle: number): Promise<void> {
    const d = Math.atan2(Math.sin(angle - this.theta), Math.cos(angle - this.theta));
    if (Math.abs(d) < 0.005) return Promise.resolve();
    this.dir = d >= 0 ? 1 : -1;
    this.remaining = Math.abs(d);
    this.addGlow(this.theta, d);
    return new Promise((r) => (this.arrive = r));
  }

  private placeTrain() {
    const t = this.theta;
    this.train.position.set(Math.cos(t) * this.radius, 0.1, Math.sin(t) * this.radius);
    // 車頭（+x）朝行進方向：逆時針的切線是 (-sin, cos)
    this.train.rotation.y = Math.atan2(-Math.cos(t), -Math.sin(t)) + (this.dir < 0 ? Math.PI : 0);
  }

  /** 開動時那段鐵道亮起來 */
  private addGlow(from: number, delta: number) {
    const n = Math.max(2, Math.ceil((Math.abs(delta) * this.radius) / 0.6));
    const pts = Array.from({ length: n + 1 }, (_, i) => {
      const a = from + (delta * i) / n;
      return new THREE.Vector3(Math.cos(a) * this.radius, 0.36, Math.sin(a) * this.radius);
    });
    const mesh = new THREE.Mesh(
      new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), n * 2, 0.09, 6, false),
      new THREE.MeshBasicMaterial({ color: 0xffa94d, transparent: true, opacity: 0.85 }),
    );
    mesh.visible = this.glowsVisible;
    this.scene.add(mesh);
    this.glows.push({ mesh, fadeAt: Infinity });
  }

  update(dt: number, now: number) {
    if (this.arrive) {
      const v = Math.min(VMAX, 0.05 + this.remaining * 0.6); // 快到站時慢下來
      const step = Math.min(this.remaining, v * dt);
      this.theta += this.dir * step;
      this.remaining -= step;
      this.placeTrain();
      if (this.remaining <= 1e-4) {
        const r = this.arrive;
        this.arrive = null;
        for (const g of this.glows) if (g.fadeAt === Infinity) g.fadeAt = now + GLOW_LINGER_MS;
        r();
      }
    }
    this.glows = this.glows.filter((g) => {
      const m = g.mesh.material as THREE.MeshBasicMaterial;
      m.opacity = now < g.fadeAt ? 0.85 : Math.max(0, 0.85 * (1 - (now - g.fadeAt) / 1200));
      if (m.opacity > 0) return true;
      g.mesh.removeFromParent();
      g.mesh.geometry.dispose();
      m.dispose();
      return false;
    });
  }

  dispose() {
    this.disposed = true;
    window.clearTimeout(this.clearTimer);
    for (const p of [...this.parcels]) this.take(p);
    this.cargo.element.remove();
  }
}
