import Phaser from "phaser";
import type { VillageEvent } from "../events";

export interface SpriteManifest {
  frameWidth: number;
  frameHeight: number;
  rows: string[]; // ["down","left","right","up"]
  walkCycle: number[];
  characters: Record<string, { name: string; path: string; desc?: string; system?: boolean }>; // system：村長、工匠專用
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
}

export const MAYOR: VillagerInfo = { id: "mayor", name: "村長", sprite: "mayor", home: [15, 9] };
/** 工匠：在「村民 → 用對話建立」幫忙打造村民，不接問答任務 */
export const CRAFTSMAN: VillagerInfo = { id: "craftsman", name: "工匠", sprite: "craftsman", home: [26, 16] };

class Villager {
  sprite: Phaser.GameObjects.Sprite;
  bubble: Phaser.GameObjects.Sprite;
  label: Phaser.GameObjects.Text;
  dir: Dir = "down";
  busy = false;
  private tween?: Phaser.Tweens.Tween;
  private timer?: Phaser.Time.TimerEvent;
  private bubbleTimer?: Phaser.Time.TimerEvent;

  constructor(
    private scene: Phaser.Scene,
    public id: string,
    private skin: string, // spritesheet key
    name: string,
    public home: Phaser.Math.Vector2,
  ) {
    this.sprite = scene.add.sprite(home.x, home.y, skin, 1).setOrigin(0.5, 0.9).setScale(CHAR_SCALE);
    this.bubble = scene.add.sprite(home.x, home.y, "bubbles", 0).setScale(2).setVisible(false);
    this.label = scene.add
      .text(home.x, home.y, name, { fontSize: "12px", color: "#fff", backgroundColor: "#0009", padding: { x: 4, y: 1 } })
      .setOrigin(0.5, 0)
      .setResolution(2);
    this.scheduleWander(Phaser.Math.Between(300, 2500));
  }

  update() {
    const { x, y } = this.sprite;
    this.sprite.setDepth(y);
    this.label.setPosition(x, y + 2).setDepth(y + 1);
    this.bubble.setPosition(x + 22, y - 62).setDepth(10_000);
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
      if (this.busy) return;
      const r = 4 * TILE;
      const t = new Phaser.Math.Vector2(
        Phaser.Math.Clamp(this.home.x + Phaser.Math.Between(-r, r), TILE, (MAP_W - 1) * TILE),
        Phaser.Math.Clamp(this.home.y + Phaser.Math.Between(-r, r), TILE * 3, (MAP_H - 1) * TILE),
      );
      this.walkTo(t, () => this.scheduleWander(Phaser.Math.Between(1500, 4500)));
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
    this.sprite.destroy();
    this.bubble.destroy();
    this.label.destroy();
  }

  /** 走到另一位村民旁邊交接，再回家附近繼續閒晃 */
  handoffTo(other: Villager) {
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

export class VillageScene extends Phaser.Scene {
  private villagers = new Map<string, Villager>();
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
    const b = this.manifest.ui.status_bubbles;
    this.load.spritesheet("bubbles", BASE + b.path, { frameWidth: b.frameWidth, frameHeight: b.frameHeight });
  }

  create() {
    this.drawGround();
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
  }

  /** 依後端村民清單增減角色（在村民管理新增或修改後，村民會淡入登場） */
  syncVillagers(list: VillagerInfo[], animate = true) {
    if (!this.ready) {
      this.pending = list;
      return;
    }
    const wanted = new Map([MAYOR, CRAFTSMAN, ...list].map((v) => [v.id, v]));
    const look = (v: VillagerInfo) => JSON.stringify([v.sprite, v.name, v.home]);
    for (const [id, v] of this.villagers) {
      const info = wanted.get(id);
      if (!info || look(info) !== this.looks.get(id)) {
        v.destroy();
        this.villagers.delete(id);
      }
    }
    for (const info of wanted.values()) {
      if (this.villagers.has(info.id)) continue;
      this.looks.set(info.id, look(info));
      const skin = info.sprite && this.manifest.characters[info.sprite] ? info.sprite : "poet";
      const [hx, hy] = info.home ?? this.freeSpot(info.id);
      const v = new Villager(this, info.id, skin, info.name, new Phaser.Math.Vector2(hx * TILE, hy * TILE));
      this.villagers.set(info.id, v);
      if (animate) v.appear();
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
      case "agent.handoff": {
        const from = this.villagers.get(e.from_agent ?? "");
        const to = this.villagers.get(e.to_agent ?? "");
        if (from && to) from.handoffTo(to);
        break;
      }
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
  }
}
