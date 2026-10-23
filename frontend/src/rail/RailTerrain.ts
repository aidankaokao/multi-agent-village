/**
 * 鐵道小鎮場景：村子是中間的小鎮，一條環狀鐵道繞著小鎮；主車站在小鎮前方（+z），旁邊的倉庫是中轉站。
 * 駐村專家住在鐵道外側自己的小站（樣式依名字），外圍是田野和遠山。
 * 主車站在鐵道內側（正面朝鏡頭）；專家的小站在鐵道外側（正面朝小鎮）。
 * 和駐村專家之間的交接都經過中轉站、由火車載送（rail/Railway.ts）。
 */
import * as THREE from "three";
import { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import type { BuildCtx, Outpost, Spot, Terrain, ViewConfig } from "../world3d/common";
import * as M from "../world3d/models";
import { TOWN_NODES, buildTown, townSpots } from "../world3d/town";
import * as R from "./models";
import { Railway } from "./Railway";

const TOWN_R = 20; // 小鎮（村民生活的範圍）的半徑
const TRACK_R = 24; // 環狀鐵道的半徑
const SITE_R = 29.5; // 專家的小站中心離原點多遠
const MAIN_ANGLE = Math.PI / 2; // 主車站在小鎮正前方（+z，鏡頭這邊）

// 專家小站的位置（角度）：避開主車站，先放鏡頭看得清楚的兩側
const SITE_ANGLES = [35, 145, -35, -145, -90, 0, 180, -60, -120].map((d) => (d * Math.PI) / 180);

/** 固定的亂數（每次蓋出來都一樣） */
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

/** 一個面向小鎮中心的「站區」：子物件用站區自己的座標（+z 朝小鎮、x 沿著鐵道） */
function site(angle: number): THREE.Group {
  const g = new THREE.Group();
  g.position.set(Math.cos(angle) * SITE_R, 0, Math.sin(angle) * SITE_R);
  g.lookAt(0, 0, 0);
  g.updateMatrixWorld(true);
  return g;
}

/** 站區座標 → 世界座標（站在地上） */
function toWorldOf(g: THREE.Group, x: number, z: number): THREE.Vector3 {
  return new THREE.Vector3(x, 0, z).applyMatrix4(g.matrixWorld).setY(0.18);
}

const LOCAL_TRACK_Z = SITE_R - TRACK_R; // 站區座標裡鐵道在哪（z）
const LOCAL_PLATFORM_Z = LOCAL_TRACK_Z - 1.6;

export class RailTerrain implements Terrain {
  view: ViewConfig = {
    dist: 82,
    polar: 0.9,
    azimuth: 0.38,
    target: new THREE.Vector3(0, 0, 5),
    minDist: 18,
    maxDist: 130,
    panLimit: 34,
    birdHeight: 125,
    fog: [110, 250],
  };
  routeNodes = TOWN_NODES;
  private clouds: THREE.Group[] = [];
  private smokes: { group: THREE.Group; puffs: THREE.Mesh[] }[] = [];
  courier?: Railway;
  private stops = new WeakMap<Outpost, number>(); // 專家的小站 → 火車停在鐵道上的哪個角度

  inside(p: THREE.Vector3, margin: number): boolean {
    return Math.hypot(p.x, p.z) <= TOWN_R - margin;
  }

  build(ctx: BuildCtx) {
    const s = ctx.scene;
    // 地面：外面的田野、小鎮的草地顏色淡一點
    const ground = new THREE.Mesh(new THREE.CircleGeometry(170, 64), M.mat(0x8fbf68, 1));
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = 0.16;
    ground.receiveShadow = true;
    s.add(ground);
    const town = new THREE.Mesh(new THREE.CircleGeometry(TOWN_R + 0.8, 64), M.mat(0x9fcd78, 1));
    town.rotation.x = -Math.PI / 2;
    town.position.y = 0.17;
    town.receiveShadow = true;
    s.add(town);
    buildTown(ctx, TOWN_R * 1.8, TOWN_R * 1.5);

    // 小鎮邊緣種一圈樹（路口留空）
    for (let i = 0; i < 30; i++) {
      const a = (i / 30) * Math.PI * 2 + 0.05;
      const r = TOWN_R - 0.9 - (i % 3) * 0.5;
      const x = Math.cos(a) * r;
      const z = Math.sin(a) * r;
      if (Math.abs(x) < 2.5 || Math.abs(z + 0.36) < 2.5) continue;
      if (z > 14 && Math.abs(x) < 9.5) continue; // 主車站與倉庫
      const t = i % 3 === 0 ? M.pine(0.9 + (i % 4) * 0.1) : M.tree(0.85 + (i % 5) * 0.08, i);
      t.position.set(x, 0.18, z);
      s.add(t);
    }

    s.add(R.track(TRACK_R));
    this.buildMainStation(ctx);

    // 田野與零星的樹：避開車站的方向
    const rand = rng(7);
    const crops = [0x6fae4a, 0x8cc05a, 0xd8c060, 0x5f9e44];
    const near = (a: number) => [MAIN_ANGLE, ...SITE_ANGLES].some((x) => Math.abs(Math.atan2(Math.sin(a - x), Math.cos(a - x))) < 0.26);
    for (let i = 0; i < 44; i++) {
      const a = (i / 22) * Math.PI * 2 + rand() * 0.12;
      if (near(a)) continue;
      const r = (i < 22 ? 36 : 47) + rand() * 7;
      const f = R.field(5 + rand() * 4, 3.5 + rand() * 3, crops[i % crops.length]);
      f.position.set(Math.cos(a) * r, 0, Math.sin(a) * r);
      f.rotation.y = -a + (rand() - 0.5) * 0.3;
      s.add(f);
    }
    for (let i = 0; i < 46; i++) {
      const a = rand() * Math.PI * 2;
      if (near(a)) continue;
      const r = 33 + rand() * 40;
      const t = rand() < 0.35 ? M.pine(0.9 + rand() * 0.4) : M.tree(0.9 + rand() * 0.4, i);
      t.position.set(Math.cos(a) * r, 0.18, Math.sin(a) * r);
      s.add(t);
    }

    // 遠山（一整圈）、雲
    const hillColors = [0x7fae6a, 0x8db878, 0x9fbf86, 0x6f9e60];
    for (let i = 0; i < 24; i++) {
      const a = (i / 24) * Math.PI * 2;
      const h = M.hill(10 + (i % 4) * 3, 9 + (i % 5) * 3.5, hillColors[i % 4]);
      h.position.set(Math.cos(a) * (120 + (i % 3) * 12), 0, Math.sin(a) * (120 + (i % 3) * 12));
      s.add(h);
    }
    for (let i = 0; i < 7; i++) {
      const cl = M.cloud();
      const a = Math.PI * 0.9 + (i / 6) * Math.PI * 1.2;
      cl.position.set(Math.cos(a) * (75 + (i % 3) * 14), 32 + (i % 3) * 5, Math.sin(a) * (75 + (i % 3) * 14));
      cl.scale.setScalar(1.6 + (i % 3) * 0.5);
      this.clouds.push(cl);
      s.add(cl);
    }
  }

  /**
   * 主車站：在鐵道內側、小鎮正前方（+z），正面朝外（鏡頭這邊），門口就是月台；旁邊的倉庫是中轉站。
   * 小鎮的直路一路通到車站後門；火車停在月台邊。
   */
  private buildMainStation(ctx: BuildCtx) {
    const s = ctx.scene;
    const pfZ = TRACK_R - 1.45; // 月台中心（靠著鐵軌內側）
    const stZ = pfZ - 2.15; // 站房中心
    const st = R.mainStation();
    st.position.set(0, 0, stZ);
    s.add(st);
    ctx.addObstacle(0, stZ, 2.7);
    const pf = R.platform(12);
    pf.position.set(-1, 0, pfZ);
    s.add(pf);
    const sign = R.signPost("村莊站", 1.8, 0.48);
    sign.position.set(3.4, 0.54, pfZ - 0.15);
    s.add(sign);
    for (const x of [-5.6, 5.0]) {
      const l = M.lamp();
      l.position.set(x, 0.54, pfZ);
      s.add(l);
    }
    // 倉庫（中轉站）：站房左邊，正面朝月台
    const dep = R.warehouse();
    dep.position.set(-5.4, 0, stZ + 0.1);
    s.add(dep);
    ctx.addObstacle(-5.4, stZ + 0.1, 1.9);
    const depSign = R.signPost("中轉站", 1.4, 0.4, "#fff4e8", "#a2421f");
    depSign.position.set(-7.7, 0, stZ + 1.0);
    depSign.rotation.y = 0.5;
    s.add(depSign);

    // 站前（後門）廣場，和小鎮的直路接起來
    s.add(M.flat(new THREE.PlaneGeometry(14, 1.6), 0xd9bf8a, -1.6, stZ - 1.9, 0.185));
    const roadFrom = TOWN_R * 0.75;
    s.add(M.flat(new THREE.PlaneGeometry(0.75, stZ - 1.9 - roadFrom), 0xd9bf8a, -0.36, (roadFrom + stZ - 1.9) / 2));

    // 小火車：平常停在月台邊；和駐村專家之間的交接由它載（Railway）
    const t = R.train();
    s.add(t);
    this.courier = new Railway(s, t, dep, (o) => this.stops.get(o), MAIN_ANGLE - 0.04, TRACK_R);
  }

  spots(ctx: BuildCtx): Spot[] {
    return townSpots(ctx, (p, m) => this.inside(p, m));
  }

  /** 第 index 位駐村專家的小站：站房（依樣式）、月台、站名、路燈、平交道；外出中掛「暫停營業」 */
  outpost(index: number, _ctx: BuildCtx, who: { id: string; name: string; skill?: string }): { outpost: Outpost; group: THREE.Group } {
    const angle = SITE_ANGLES[index % SITE_ANGLES.length];
    const g = site(angle);
    const style = R.styleFor(`${who.name} ${who.skill ?? ""}`);
    const st = R.station(style, index);
    st.position.z = -0.7;
    g.add(st);
    if (st.userData.smoke) this.smokes.push({ group: g, puffs: st.userData.smoke as THREE.Mesh[] });
    const pf = R.platform(5.4);
    pf.position.z = LOCAL_PLATFORM_Z;
    g.add(pf);
    g.add(M.flat(new THREE.PlaneGeometry(5.4, 2.2), 0xd9bf8a, 0, 2.0, 0.185));
    const sign = R.signPost(who.name, 1.7, 0.42);
    sign.position.set(1.3, 0.54, LOCAL_PLATFORM_Z - 0.25);
    g.add(sign);
    const l = M.lamp();
    l.position.set(-2.2, 0.54, LOCAL_PLATFORM_Z);
    g.add(l);
    const cr = R.crossing();
    cr.position.set(-3.4, 0, LOCAL_TRACK_Z);
    g.add(cr);
    // 外出中：站房上方掛「暫停營業」（HTML 標籤，從哪個角度都看得清楚）
    const el = document.createElement("div");
    el.className = "isl-closed";
    el.textContent = "🚧 暫停營業";
    const closed = new CSS2DObject(el);
    closed.position.set(0, 3.6, -0.7);
    closed.visible = false;
    g.add(closed);
    g.userData.closedSign = closed;
    const t = M.tree(0.85, index + 3);
    t.position.set(3.4, 0, -1.4);
    g.add(t);

    const center = toWorldOf(g, 0, 1.9);
    const toTown = toWorldOf(g, 0, 8);
    const door: Spot = { kind: "house", pos: toWorldOf(g, -0.9, 0.9), face: toTown, taken: null };
    const desk: Spot = { kind: "tree", pos: toWorldOf(g, 1.5, 1.5), face: toTown, taken: null };
    // 走到小鎮：站前 → 平交道旁的月台尾端 → 越過鐵道（鐵道內側）
    const gates = [toWorldOf(g, -3.4, 3.0), toWorldOf(g, -3.4, LOCAL_TRACK_Z + 2.4)];
    const outpost: Outpost = { center, radius: 4.0, gates, door, desk };
    this.stops.set(outpost, angle);
    return { outpost, group: g };
  }

  outpostAway(group: THREE.Group, away: boolean) {
    const sign = group.userData.closedSign as THREE.Object3D | undefined;
    if (sign) sign.visible = away;
  }

  tint() {
    // 沒有海；天色、燈光由 World3D 處理
  }

  update(dt: number, now: number) {
    for (const c of this.clouds) {
      c.position.x += dt * 0.5;
      if (c.position.x > 110) c.position.x = -110;
    }
    this.smokes = this.smokes.filter((x) => x.group.parent); // 小站拆掉了
    for (const { group, puffs } of this.smokes) {
      const off = !!group.userData.away;
      for (const p of puffs) {
        const t = (now / 3200 + (p.userData.phase as number)) % 1;
        p.visible = !off;
        p.position.set(1.25 + t * 0.6, 3.5 + t * 2.2, -0.55 - t * 0.3);
        p.scale.setScalar(0.6 + t * 1.4);
        (p.material as THREE.MeshStandardMaterial).opacity = 0.7 * (1 - t);
      }
    }
  }
}

