/**
 * 3D 村子裡的一位村民：Q 版模型、名牌 / 冒泡 / 狀態（HTML）、走路、派工、交接、聊天、生活作息（姿勢與道具）。
 */
import * as THREE from "three";
import { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { nextActivity, periodOf, type Activity, type Pose } from "../village/life";
import { SITTING, SPEED, TAG_Y, VILLAGER_SCALE, between, pick, type Outpost, type Spot } from "./common";
import * as M from "./models";
import type { World3D } from "./World3D";

export type Bubble = "working" | "done" | "error" | "thinking";
const BUBBLE: Record<Bubble, string> = { working: "⚙️", thinking: "💭", done: "✅", error: "❗" };

export class Villager3D {
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
  private onErrand = false;
  private working = false; // 派工中（startWork 到 finish）
  busy = false;
  chatting = false;
  away = false;
  lines: string[] = [];
  skill = "";
  speaking = false;

  constructor(
    private world: World3D,
    public id: string,
    public name: string,
    public sprite: string,
    public home: THREE.Vector3,
    public outpost: Outpost | null,
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

  /** 走過去（村子裡會繞開池塘、房子；駐村專家在自己住的地方） */
  private goTo(target: THREE.Vector3, onDone: () => void) {
    if (this.outpost) return this.walkTo(target, onDone, this.outpost);
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

  /** 走一段路（進出駐村專家住的地方會經過它的出入口，例如橋）；到了呼叫 onDone */
  walkTo(target: THREE.Vector3, onDone?: () => void, targetOutpost: Outpost | null = null) {
    const points: THREE.Vector3[] = [];
    const from = this.world.outpostAt(this.pos);
    if (from && from !== targetOutpost) points.push(...from.gates.map((g) => g.clone()));
    if (targetOutpost && targetOutpost !== from) points.push(...[...targetOutpost.gates].reverse().map((g) => g.clone()));
    points.push(target.clone());
    this.path = points;
    this.onArrive = onDone;
  }

  private wander(now: number) {
    this.wanderAt = now + 1500 + Math.random() * 3000;
    if (this.away) return;
    const target = this.world.wanderTarget(this.home, this.outpost, this.pos);
    if (target) this.walkTo(target, undefined, this.outpost);
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
    this.busy = this.working = true;
    if (!this.onErrand) {
      this.stop();
      this.faceTo(this.world.cameraGround());
    }
    this.showBubble(kind);
  }

  finish(ok: boolean) {
    this.showBubble(ok ? "done" : "error", ok ? 1800 : 4000);
    this.working = false;
    this.busy = this.onErrand; // 跑腿中（去中轉站放 / 拿東西）就跑完再說
    this.wanderAt = performance.now() + 2000;
  }

  /**
   * 跑腿：走到 target（例如中轉站），到了呼叫 onArrive，停一下再回去過日子。
   * 去的路上頭上拿著 take（放東西）；回來的路上拿著 bring（取東西）。工作中也照樣走完。
   */
  errand(target: THREE.Vector3, face: THREE.Vector3, onArrive: () => void, take: string | null = null, bring: string | null = null) {
    this.endChat();
    this.interrupt();
    this.busy = true;
    this.onErrand = true;
    this.carry(take);
    const path = this.outpost ? null : this.world.route(this.pos, target);
    const arrived = () => {
      this.faceTo(face);
      this.carry(bring);
      onArrive();
      this.timers.push(window.setTimeout(() => {
        this.onErrand = false;
        this.busy = this.working;
        if (bring) this.timers.push(window.setTimeout(() => this.carry(null), 2600));
        if (!this.working) this.wanderAt = performance.now() + 300; // 回去接著做剛才的事
      }, 700));
    };
    if (path) {
      this.path = path;
      this.onArrive = arrived;
    } else this.walkTo(target, arrived, null);
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
    }, other.outpost);
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
        else this.walkTo(this.world.wanderTarget(this.home, this.outpost, this.home) ?? this.home.clone(), undefined, this.outpost);
      }, 800));
    }, other.outpost);
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

// ---------------------------------------------------------------- 閒聊
