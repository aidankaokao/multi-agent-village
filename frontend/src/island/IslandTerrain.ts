/**
 * 島嶼場景：村子在一座圓角六邊形的主島上，四周是海；駐村專家住在外圍的小島，用木棧橋連到主島（交接時沿橋走）。
 * 村民、日夜、協作線、事件都在 world3d/World3D.ts，這裡只有地形與駐村專家住的地方。
 */
import * as THREE from "three";
import type { BuildCtx, Outpost, Spot, Terrain, ViewConfig } from "../world3d/common";
import * as M from "../world3d/models";
import { TOWN_NODES, buildTown, townSpots } from "../world3d/town";

const ISLAND_R = 19.5; // 主島（平頂六邊形）的外接圓半徑
const ISLET_DIST = 27; // 小島中心離主島中心多遠
const ISLET_R = 3.6;

// 外圍小島的位置（角度）：先放在鏡頭看得清楚的前方與兩側
const ISLET_ANGLES = [20, -20, 60, -60, 100, -100, 140, -140, 180].map((d) => (d * Math.PI) / 180);

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

export class IslandTerrain implements Terrain {
  view: ViewConfig = {
    dist: 78,
    polar: 0.9,
    azimuth: 0.38,
    target: new THREE.Vector3(0, 0, 1.5),
    minDist: 18,
    maxDist: 110,
    panLimit: 26,
    birdHeight: 105,
    fog: [90, 210],
  };
  routeNodes = TOWN_NODES;
  private seaMat!: THREE.MeshStandardMaterial;
  private boats: THREE.Group[] = [];
  private clouds: THREE.Group[] = [];

  inside(p: THREE.Vector3, margin: number): boolean {
    return insideHex(p, ISLAND_R, margin);
  }

  build(ctx: BuildCtx) {
    const s = ctx.scene;
    const sea = M.sea(260);
    this.seaMat = sea.material as THREE.MeshStandardMaterial;
    s.add(sea);
    s.add(M.island(ISLAND_R));
    buildTown(ctx, ISLAND_R * 1.7, ISLAND_R * 1.55);

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

  spots(ctx: BuildCtx): Spot[] {
    return townSpots(ctx, (p, m) => this.inside(p, m));
  }

  /** 第 index 座外圍小島：島、木棧橋、小屋、一棵樹 */
  outpost(index: number): { outpost: Outpost; group: THREE.Group } {
    const angle = ISLET_ANGLES[index % ISLET_ANGLES.length];
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
    const hut = M.house(0xf6efe0, [0x2f7f7a, 0x8a3a6a, 0xc8743a, 0x4a6a9a][index % 4]);
    hut.scale.setScalar(0.7);
    const back = dir.clone().multiplyScalar(ISLET_DIST + 1.6);
    hut.position.set(back.x, 0.18, back.z);
    hut.lookAt(0, 0.18, 0);
    g.add(hut);
    const t = M.tree(0.8, index);
    const side = new THREE.Vector3(-dir.z, 0, dir.x).multiplyScalar(2.2).add(center);
    t.position.set(side.x, 0.18, side.z);
    g.add(t);
    const toMain = center.clone().multiplyScalar(0.5); // 面向主島（也就是鏡頭這邊）
    const door: Spot = { kind: "house", pos: dir.clone().multiplyScalar(ISLET_DIST + 0.3).setY(0.18), face: toMain, taken: null };
    const desk: Spot = { kind: "tree", pos: new THREE.Vector3(-dir.z, 0, dir.x).multiplyScalar(-1.1).add(center).setY(0.18), face: toMain, taken: null };
    return { outpost: { center, radius: ISLET_R + 0.5, gates: [gateIslet, gateIsland], door, desk }, group: g };
  }

  tint(water: THREE.Color) {
    this.seaMat.color.copy(water);
  }

  update(dt: number, now: number) {
    for (const b of this.boats) {
      const ph = b.userData.phase as number;
      b.position.y = -0.45 + Math.sin(now / 900 + ph) * 0.06;
      b.rotation.z = Math.sin(now / 1100 + ph) * 0.05;
    }
    for (const c of this.clouds) {
      c.position.x += dt * 0.5;
      if (c.position.x > 110) c.position.x = -110;
    }
  }
}
