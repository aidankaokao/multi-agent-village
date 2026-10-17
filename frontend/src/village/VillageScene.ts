import Phaser from "phaser";
import type { VillageEvent } from "../events";

export interface SpriteManifest {
  frameWidth: number;
  frameHeight: number;
  rows: string[]; // ["down","left","right","up"]
  walkCycle: number[];
  characters: Record<string, { name: string; path: string; desc?: string; system?: boolean }>; // system：村長、工匠專用
  decor?: Record<string, { path: string; width: number; height: number }>;
  ui: { status_bubbles: { path: string; frameWidth: number; frameHeight: number } };
}

const BASE = "/assets/sprites/";
export const TILE = 32;
export const MAP_W = 30;
export const MAP_H = 20;
const SPEED = 60; // px/s
const CHAR_SCALE = 2; // 32px 角色放大 2 倍（整數倍，像素才不會糊）

type Dir = "down" | "left" | "right" | "up";

/** 村裡要出現的村民；來自後端 /api/agents（agent.yaml 的 x-village），村長由前端固定加入 */
export interface VillagerInfo {
  id: string;
  name: string;
  sprite: string | null;
  home: [number, number] | null;
  away?: boolean; // 駐村專家連不上（外出中）：半透明、名字後面標示
  lines?: string[]; // 閒著時偶爾冒出的短句（從名片產生，10 個字左右）
  skill?: string; // 一句話的專長（村民互動時別人會提到），例如「寫詩」
}

// ---------------------------------------------------------------- 地圖裝飾與障礙物

const DECOR_SCALE = 2; // 和角色一樣放大 2 倍

/** 每種裝飾擋住的範圍（格數，以底部中央為基準）；0 = 可以踩過去（花） */
const DECOR_BLOCK: Record<string, [number, number]> = {
  tree: [1, 1], // 只擋樹幹，從樹冠後面走過會被遮住
  pine: [1, 1],
  bush: [2, 1],
  rock: [1, 1],
  well: [2, 1],
  house_red: [4, 3],
  house_blue: [4, 3],
  sign: [1, 1],
  workbench: [2, 1],
  flowers: [0, 0],
};

/** x = 中心（格，可以是 .5），y = 底邊（格）。地圖上緣（第 0～2 列）村民不會走到，大型裝飾集中在那裡。 */
export const DECOR: { kind: string; x: number; y: number }[] = [
  { kind: "tree", x: 1.5, y: 3 },
  { kind: "house_red", x: 6, y: 4 },
  { kind: "tree", x: 10.5, y: 3 },
  { kind: "pine", x: 12.5, y: 3 },
  { kind: "tree", x: 17.5, y: 3 },
  { kind: "house_blue", x: 21, y: 4 },
  { kind: "tree", x: 25.5, y: 3 },
  { kind: "pine", x: 28.5, y: 3 },
  { kind: "pine", x: 0.5, y: 14 },
  { kind: "tree", x: 29.5, y: 13 },
  { kind: "tree", x: 7.5, y: 20 },
  { kind: "well", x: 18, y: 13 },
  { kind: "sign", x: 12.5, y: 12 },
  { kind: "workbench", x: 28, y: 16 },
  { kind: "bush", x: 11, y: 18 },
  { kind: "bush", x: 22, y: 19 },
  { kind: "rock", x: 19.5, y: 15 },
  { kind: "flowers", x: 4.5, y: 12 },
  { kind: "flowers", x: 9.5, y: 15 },
  { kind: "flowers", x: 24.5, y: 12 },
  { kind: "flowers", x: 17.5, y: 18 },
  { kind: "flowers", x: 11.5, y: 6 },
];

/** 左下角的池塘（格子範圍，含頭尾） */
export const POND = { x0: 2, y0: 15, x1: 5, y1: 17 };

const key = (x: number, y: number) => `${x},${y}`;

export const BLOCKED: Set<string> = (() => {
  const b = new Set<string>();
  for (const d of DECOR) {
    const [w, h] = DECOR_BLOCK[d.kind] ?? [1, 1];
    const left = Math.round(d.x - w / 2);
    for (let x = left; x < left + w; x++) for (let y = d.y - h; y < d.y; y++) b.add(key(x, y));
  }
  for (let x = POND.x0; x <= POND.x1; x++) for (let y = POND.y0; y <= POND.y1; y++) b.add(key(x, y));
  return b;
})();

export const isBlocked = (x: number, y: number) => BLOCKED.has(key(x, y));

/** 住處剛好在障礙物上時，挪到最近的空地 */
export function nearestFree([hx, hy]: [number, number]): [number, number] {
  if (!isBlocked(hx, hy)) return [hx, hy];
  for (let r = 1; r < Math.max(MAP_W, MAP_H); r++) {
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        const x = hx + dx;
        const y = hy + dy;
        if (x >= 1 && x <= MAP_W - 2 && y >= 3 && y <= MAP_H - 2 && !isBlocked(x, y)) return [x, y];
      }
    }
  }
  return [hx, hy];
}

/** 像素座標的一段直線會不會穿過障礙物（村民腳的位置） */
function pathBlocked(ax: number, ay: number, bx: number, by: number): boolean {
  const steps = Math.ceil(Math.hypot(bx - ax, by - ay) / 12);
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    if (isBlocked(Math.floor((ax + (bx - ax) * t) / TILE), Math.floor((ay + (by - ay) * t) / TILE))) return true;
  }
  return false;
}

export const MAYOR: VillagerInfo = { id: "mayor", name: "村長", sprite: "mayor", home: [15, 9], lines: ["有問題儘管問！", "我來分派工作", "點我開始問答"], skill: "分派工作" };
/** 工匠：在「村民 → 用對話建立」幫忙打造村民，不接問答任務 */
export const CRAFTSMAN: VillagerInfo = { id: "craftsman", name: "工匠", sprite: "craftsman", home: [26, 16], lines: ["想要新村民嗎？", "我會打造村民", "敲敲打打～"], skill: "打造村民" };
/** 審核員：最終答案送出前檢查（可在「村規」關閉），事件的 agent 是 reviewer */
export const REVIEWER: VillagerInfo = { id: "reviewer", name: "審核員", sprite: "auditor", home: [10, 12], lines: ["答案我會把關", "仔細檢查中…"], skill: "檢查答案" };
/** 書記：問答後在背景記下使用者的長期記憶，事件的 agent 是 scribe */
export const SCRIBE: VillagerInfo = { id: "scribe", name: "書記", sprite: "scribe", home: [13, 15], lines: ["我會記住你說的", "記下來了～"], skill: "記事情" };
/** 鑑定師：使用者附圖時先看圖，把內容寫成文字交給村長，事件的 agent 是 appraiser */
export const APPRAISER: VillagerInfo = { id: "appraiser", name: "鑑定師", sprite: "appraiser", home: [19, 12], lines: ["有圖給我看看", "我很會看圖喔"], skill: "看圖" };

class Villager {
  sprite: Phaser.GameObjects.Sprite;
  bubble: Phaser.GameObjects.Sprite;
  label: Phaser.GameObjects.Text;
  dir: Dir = "down";
  busy = false;
  private tween?: Phaser.Tweens.Tween;
  private timer?: Phaser.Time.TimerEvent;
  private bubbleTimer?: Phaser.Time.TimerEvent;

  away = false;
  chatting = false; // 正在和別的村民聊天
  lines: string[] = [];
  skill = "";
  private speech?: Phaser.GameObjects.Text;
  private speechTimer?: Phaser.Time.TimerEvent;
  private chatTimers: Phaser.Time.TimerEvent[] = [];

  constructor(
    private scene: Phaser.Scene,
    public id: string,
    private skin: string, // spritesheet key
    public name: string,
    public home: Phaser.Math.Vector2,
    onClick?: (id: string) => void,
  ) {
    this.sprite = scene.add.sprite(home.x, home.y, skin, 1).setOrigin(0.5, 0.9).setScale(CHAR_SCALE);
    this.bubble = scene.add.sprite(home.x, home.y, "bubbles", 0).setScale(2).setVisible(false);
    this.label = scene.add
      .text(home.x, home.y, name, { fontSize: "12px", color: "#fff", backgroundColor: "#0009", padding: { x: 4, y: 1 } })
      .setOrigin(0.5, 0)
      .setResolution(2);
    if (onClick) {
      this.sprite.setInteractive({ useHandCursor: true }).on("pointerdown", () => onClick(this.id));
    }
    this.scheduleWander(Phaser.Math.Between(300, 2500));
  }

  /** 閒著：可以冒泡、聊天（沒在工作、沒在聊天、沒外出） */
  get idle(): boolean {
    return !this.busy && !this.chatting && !this.away;
  }

  get speaking(): boolean {
    return !!this.speech;
  }

  get pos(): Phaser.Math.Vector2 {
    return new Phaser.Math.Vector2(this.sprite.x, this.sprite.y);
  }

  update() {
    const { x, y } = this.sprite;
    this.sprite.setDepth(y);
    this.label.setPosition(x, y + 2).setDepth(y + 1);
    this.bubble.setPosition(x + 22, y - 62).setDepth(10_000);
    this.speech?.setPosition(x, y - 66).setDepth(10_001);
  }

  /** 頭上冒一句話（幾秒後淡出） */
  say(text: string, ms = 3200) {
    this.speechTimer?.remove();
    this.speech?.destroy();
    this.speech = this.scene.add
      .text(this.sprite.x, this.sprite.y - 66, text, {
        fontSize: "12px",
        color: "#2b2a27",
        backgroundColor: "#fffdf6",
        padding: { x: 6, y: 3 },
      })
      .setOrigin(0.5, 1)
      .setResolution(2)
      .setDepth(10_001);
    const s = this.speech;
    this.speechTimer = this.scene.time.delayedCall(ms, () => {
      this.scene.tweens.add({
        targets: s,
        alpha: 0,
        duration: 400,
        onComplete: () => {
          s.destroy();
          if (this.speech === s) this.speech = undefined;
        },
      });
    });
  }

  /** 面向某個點（不走路） */
  faceTo(p: Phaser.Math.Vector2) {
    const dx = p.x - this.sprite.x;
    const dy = p.y - this.sprite.y;
    this.face(Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : dy > 0 ? "down" : "up", false);
  }

  /** 停下來回應使用者（被點到）：轉向前方、冒泡 */
  greet(text: string) {
    if (!this.busy && !this.chatting) {
      this.tween?.stop();
      this.face("down", false);
      this.scheduleWander(4000);
    }
    this.say(text, 2600);
  }

  /** 走到另一位村民旁邊聊幾句再散開；中途被派工就中斷 */
  chatWith(other: Villager, script: [string, string, string?]) {
    this.chatting = other.chatting = true;
    this.timer?.remove();
    other.timer?.remove();
    other.tween?.stop();
    other.face("down", false);
    const side = this.sprite.x < other.sprite.x ? -1 : 1;
    const spot = new Phaser.Math.Vector2(other.sprite.x + side * 64, other.sprite.y); // 站開一點，名字才不會疊在一起
    this.walkTo(spot, () => {
      if (!this.chatting) return; // 走路途中被派工
      this.faceTo(other.pos);
      other.faceTo(this.pos);
      const lines: [Villager, string | undefined][] = [[this, script[0]], [other, script[1]], [this, script[2]]];
      lines.forEach(([who, text], i) => {
        if (!text) return;
        const t = this.scene.time.delayedCall(i * 1700, () => who.say(text, 1800));
        this.chatTimers.push(t);
      });
      this.chatTimers.push(this.scene.time.delayedCall(lines.filter((l) => l[1]).length * 1700 + 600, () => {
        this.endChat();
        other.endChat();
      }));
    });
  }

  endChat() {
    this.chatTimers.forEach((t) => t.remove());
    this.chatTimers = [];
    if (!this.chatting) return;
    this.chatting = false;
    if (!this.busy) this.scheduleWander(Phaser.Math.Between(800, 2500));
  }

  private face(dir: Dir, walking: boolean) {
    this.dir = dir;
    if (walking) this.sprite.play(`${this.skin}-walk-${dir}`, true);
    else {
      this.sprite.stop();
      this.sprite.setFrame(["down", "left", "right", "up"].indexOf(dir) * 3 + 1);
    }
  }

  walkTo(target: Phaser.Math.Vector2, onDone?: () => void) {
    this.tween?.stop();
    const dx = target.x - this.sprite.x;
    const dy = target.y - this.sprite.y;
    const dist = Math.hypot(dx, dy);
    if (dist < 2) return onDone?.();
    this.face(Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : dy > 0 ? "down" : "up", true);
    this.tween = this.scene.tweens.add({
      targets: this.sprite,
      x: target.x,
      y: target.y,
      duration: (dist / SPEED) * 1000,
      onComplete: () => {
        this.face(this.dir, false);
        onDone?.();
      },
    });
  }

  private scheduleWander(delay: number) {
    this.timer?.remove();
    this.timer = this.scene.time.delayedCall(delay, () => {
      if (this.busy || this.chatting) return;
      const r = 4 * TILE;
      const { x, y } = this.sprite;
      // 挑一個不在障礙物上、路線也不會穿過障礙物的點；挑不到就原地休息一下
      for (let i = 0; i < 12; i++) {
        const t = new Phaser.Math.Vector2(
          Phaser.Math.Clamp(this.home.x + Phaser.Math.Between(-r, r), TILE, (MAP_W - 1) * TILE),
          Phaser.Math.Clamp(this.home.y + Phaser.Math.Between(-r, r), TILE * 3, (MAP_H - 1) * TILE),
        );
        if (!pathBlocked(x, y, t.x, t.y)) return this.walkTo(t, () => this.scheduleWander(Phaser.Math.Between(1500, 4500)));
      }
      this.scheduleWander(Phaser.Math.Between(1500, 4500));
    });
  }

  showBubble(kind: "working" | "done" | "error" | "thinking", ms?: number) {
    this.bubbleTimer?.remove();
    this.bubble.setVisible(true);
    if (kind === "working") this.bubble.play("bubble-working");
    else {
      this.bubble.stop();
      this.bubble.setFrame({ error: 4, done: 5, thinking: 6 }[kind]);
    }
    if (ms) this.bubbleTimer = this.scene.time.delayedCall(ms, () => this.bubble.setVisible(false));
  }

  startWork(kind: "working" | "thinking" = "working") {
    this.endChat(); // 正在聊天也立刻去工作
    this.busy = true;
    this.timer?.remove();
    this.tween?.stop();
    this.face("down", false);
    this.showBubble(kind);
  }

  finish(ok: boolean) {
    this.showBubble(ok ? "done" : "error", ok ? 1800 : 4000);
    this.busy = false;
    this.scheduleWander(2000);
  }

  /** 駐村專家外出中：半透明、名字加註；回來時恢復 */
  setAway(away: boolean) {
    if (away === this.away) return;
    this.away = away;
    this.sprite.setAlpha(away ? 0.35 : 1);
    this.label.setAlpha(away ? 0.6 : 1).setText(away ? `${this.name}（外出中）` : this.name);
  }

  /** 新村民登場：淡入 + 完成泡泡 */
  appear() {
    for (const o of [this.sprite, this.label]) {
      o.setAlpha(0);
      this.scene.tweens.add({ targets: o, alpha: 1, duration: 800 });
    }
    this.showBubble("done", 2000);
  }

  destroy() {
    this.tween?.stop();
    this.timer?.remove();
    this.bubbleTimer?.remove();
    this.speechTimer?.remove();
    this.chatTimers.forEach((t) => t.remove());
    this.speech?.destroy();
    this.sprite.destroy();
    this.bubble.destroy();
    this.label.destroy();
  }

  /** 走到另一位村民旁邊交接，再回家附近繼續閒晃 */
  handoffTo(other: Villager) {
    this.endChat();
    other.endChat();
    this.busy = true;
    this.timer?.remove();
    const side = this.sprite.x < other.sprite.x ? -1 : 1;
    const spot = new Phaser.Math.Vector2(other.sprite.x + side * 48, other.sprite.y);
    this.walkTo(spot, () => {
      this.face(side < 0 ? "right" : "left", false);
      this.scene.time.delayedCall(800, () => {
        this.busy = false;
        this.scheduleWander(0);
      });
    });
  }
}

// ---------------------------------------------------------------- 閒聊

const AMBIENT_MS: [number, number] = [8000, 15000]; // 每隔幾秒挑一位閒著的村民冒一句
const CHAT_MS: [number, number] = [30000, 60000]; // 每隔幾秒挑兩位聊幾句
const MAX_SPEAKING = 2; // 同時最多幾個人在冒泡
const CHAT_RANGE = 10 * TILE; // 太遠的不湊在一起聊

const GREETINGS: [string, string, string?][] = [
  ["嗨！", "嗨～最近好嗎？", "還不錯！"],
  ["今天好忙啊", "我也是…"],
  ["天氣真好", "適合散步～"],
  ["有人找你喔", "真的嗎？我去看看"],
  ["吃飽了嗎？", "剛吃完！"],
];

function chatScript(b: Villager): [string, string, string?] {
  if (b.skill && Math.random() < 0.6) {
    const ask = `${b.name}，你會${b.skill}對吧？`;
    return [ask.length > 16 ? `你會${b.skill}對吧？` : ask, "對啊，交給我！", "下次找你幫忙"];
  }
  return Phaser.Utils.Array.GetRandom(GREETINGS);
}

export class VillageScene extends Phaser.Scene {
  private villagers = new Map<string, Villager>();
  onVillagerClick?: (id: string) => void; // 點村民（App 決定要開問答還是讓他自我介紹）
  private nextAmbient = 0;
  private nextChat = 0;
  private looks = new Map<string, string>(); // 外觀 / 名字 / 住處，變了就重新登場
  private ready = false;
  private pending: VillagerInfo[] | null = null;

  constructor(private manifest: SpriteManifest) {
    super("village");
  }

  preload() {
    const { frameWidth, frameHeight } = this.manifest;
    for (const [id, c] of Object.entries(this.manifest.characters)) {
      this.load.spritesheet(id, BASE + c.path, { frameWidth, frameHeight });
    }
    for (const [id, d] of Object.entries(this.manifest.decor ?? {})) this.load.image(`deco-${id}`, BASE + d.path);
    const b = this.manifest.ui.status_bubbles;
    this.load.spritesheet("bubbles", BASE + b.path, { frameWidth: b.frameWidth, frameHeight: b.frameHeight });
  }

  create() {
    this.drawGround();
    this.drawDecor();
    this.anims.create({
      key: "bubble-working",
      frames: this.anims.generateFrameNumbers("bubbles", { frames: [0, 1, 2, 3] }),
      frameRate: 5,
      repeat: -1,
    });
    for (const id of Object.keys(this.manifest.characters)) {
      this.manifest.rows.forEach((dir, row) => {
        this.anims.create({
          key: `${id}-walk-${dir}`,
          frames: this.anims.generateFrameNumbers(id, { frames: this.manifest.walkCycle.map((f) => row * 3 + f) }),
          frameRate: 6,
          repeat: -1,
        });
      });
    }
    this.ready = true;
    this.syncVillagers(this.pending ?? [], false);
    this.nextAmbient = this.time.now + Phaser.Math.Between(...AMBIENT_MS);
    this.nextChat = this.time.now + Phaser.Math.Between(...CHAT_MS);
    this.time.addEvent({ delay: 1000, loop: true, callback: () => this.tickIdle() });
  }

  /** 閒聊排程：偶爾有村民冒一句自我介紹、偶爾兩位村民湊在一起聊天 */
  private tickIdle() {
    const now = this.time.now;
    const all = [...this.villagers.values()];
    if (now >= this.nextAmbient) {
      this.nextAmbient = now + Phaser.Math.Between(...AMBIENT_MS);
      const free = all.filter((v) => v.idle && !v.speaking && v.lines.length);
      if (free.length && all.filter((v) => v.speaking).length < MAX_SPEAKING) {
        const v = Phaser.Utils.Array.GetRandom(free);
        v.say(Phaser.Utils.Array.GetRandom(v.lines));
      }
    }
    if (now >= this.nextChat) {
      this.nextChat = now + Phaser.Math.Between(...CHAT_MS);
      const free = all.filter((v) => v.idle && !v.speaking);
      const a = free.length > 1 ? Phaser.Utils.Array.GetRandom(free) : undefined;
      const near = a ? free.filter((v) => v !== a && Phaser.Math.Distance.BetweenPoints(v.pos, a.pos) < CHAT_RANGE) : [];
      if (a && near.length) {
        const b = Phaser.Utils.Array.GetRandom(near);
        a.chatWith(b, chatScript(b));
      }
    }
  }

  /** 讓某位村民說一句話（例如被點到時的回應） */
  say(id: string, text: string) {
    this.villagers.get(id)?.greet(text);
  }

  /** 依後端村民清單增減角色（在村民管理新增或修改後，村民會淡入登場） */
  syncVillagers(list: VillagerInfo[], animate = true) {
    if (!this.ready) {
      this.pending = list;
      return;
    }
    const wanted = new Map([MAYOR, CRAFTSMAN, REVIEWER, SCRIBE, APPRAISER, ...list].map((v) => [v.id, v]));
    const look = (v: VillagerInfo) => JSON.stringify([v.sprite, v.name, v.home]);
    for (const [id, v] of this.villagers) {
      const info = wanted.get(id);
      if (!info || look(info) !== this.looks.get(id)) {
        v.destroy();
        this.villagers.delete(id);
      }
    }
    for (const info of wanted.values()) {
      const existing = this.villagers.get(info.id);
      if (existing) {
        existing.setAway(!!info.away); // 在村 / 外出中切換不用重建角色
        existing.lines = info.lines ?? [];
        existing.skill = info.skill ?? "";
        continue;
      }
      this.looks.set(info.id, look(info));
      const skin = info.sprite && this.manifest.characters[info.sprite] ? info.sprite : "poet";
      const [hx, hy] = nearestFree(info.home ?? this.freeSpot(info.id));
      const v = new Villager(this, info.id, skin, info.name, new Phaser.Math.Vector2(hx * TILE, hy * TILE), (id) =>
        this.onVillagerClick?.(id),
      );
      v.lines = info.lines ?? [];
      v.skill = info.skill ?? "";
      this.villagers.set(info.id, v);
      if (animate && !info.away) v.appear();
      v.setAway(!!info.away);
    }
  }

  /** 沒指定 home 時，依 id 算出固定的空地位置 */
  private freeSpot(id: string): [number, number] {
    let h = 0;
    for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return [3 + (h % (MAP_W - 6)), 3 + ((h >>> 8) % (MAP_H - 6))];
  }

  update() {
    this.villagers.forEach((v) => v.update());
  }

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
        v?.finish(true);
        break;
      case "agent.error":
      case "workshop.error":
        v?.finish(false);
        break;
      case "review.finished":
        v?.finish(e.passed !== false);
        break;
      case "memory.finished":
        v?.finish(true);
        break;
      case "appraise.finished":
        v?.finish(e.ok !== false);
        break;
      case "run.cancelled": // 使用者按停止：大家收工
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

  /** 裝飾：底部中央對齊，依底邊 y 排景深，村民走到後面會被擋住 */
  private drawDecor() {
    for (const d of DECOR) {
      if (!this.textures.exists(`deco-${d.kind}`)) continue; // 舊版 manifest 沒有裝飾
      const y = d.y * TILE;
      this.add.image(d.x * TILE, y, `deco-${d.kind}`).setOrigin(0.5, 1).setScale(DECOR_SCALE).setDepth(d.kind === "flowers" ? 0 : y);
    }
  }

  /** 程式產生的草地與小路；之後換成 Tiled 地圖 + CC0 tileset */
  private drawGround() {
    const g = this.add.graphics();
    const rnd = new Phaser.Math.RandomDataGenerator(["village"]);
    const grass = [0x6fae5c, 0x67a654, 0x74b462];
    for (let ty = 0; ty < MAP_H; ty++) {
      for (let tx = 0; tx < MAP_W; tx++) {
        g.fillStyle(rnd.pick(grass)).fillRect(tx * TILE, ty * TILE, TILE, TILE);
        for (let i = 0; i < 3; i++) {
          g.fillStyle(0x5a9447).fillRect(tx * TILE + rnd.between(0, 29), ty * TILE + rnd.between(0, 29), 2, 3);
        }
        if (rnd.frac() < 0.04) {
          g.fillStyle(rnd.pick([0xf2c230, 0xffffff, 0xe86a92])).fillRect(tx * TILE + rnd.between(4, 26), ty * TILE + rnd.between(4, 26), 3, 3);
        }
      }
    }
    // 十字小路 + 廣場
    g.fillStyle(0xc9a46a);
    g.fillRect(0, 9 * TILE + 8, MAP_W * TILE, TILE - 8);
    g.fillRect(15 * TILE - 8, 0, TILE - 8, MAP_H * TILE);
    g.fillCircle(15 * TILE, 9 * TILE + 20, 70);
    g.fillStyle(0xb48d55);
    for (let i = 0; i < 160; i++) g.fillRect(rnd.between(0, MAP_W * TILE), 9 * TILE + rnd.between(10, 30), 2, 2);
    // 池塘：岸邊 → 水 → 反光 → 荷葉
    const cx = ((POND.x0 + POND.x1 + 1) / 2) * TILE;
    const cy = ((POND.y0 + POND.y1 + 1) / 2) * TILE;
    const w = (POND.x1 - POND.x0 + 1) * TILE;
    const h = (POND.y1 - POND.y0 + 1) * TILE;
    g.fillStyle(0x8a7a52).fillEllipse(cx, cy, w + 8, h + 8);
    g.fillStyle(0x3a6fc4).fillEllipse(cx, cy, w, h);
    g.fillStyle(0x4f86d8).fillEllipse(cx - 10, cy - 8, w - 40, h - 36);
    g.fillStyle(0xcfe3ff);
    g.fillRect(cx - 30, cy - 14, 10, 2).fillRect(cx + 12, cy + 6, 14, 2);
    g.fillStyle(0x3f9a48).fillCircle(cx + 30, cy - 14, 6).fillCircle(cx - 36, cy + 14, 5);
    g.fillStyle(0xe86a92).fillRect(cx + 29, cy - 16, 3, 3);
  }
}
