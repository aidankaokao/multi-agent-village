/**
 * 村子本身（主島或鐵道小鎮中間的小鎮）：十字路與廣場、裝飾（位置和像素版一樣）、池塘、路燈，以及生活作息的地點。
 */
import * as THREE from "three";
import type { SpotKind } from "../village/life";
import { DECOR, POND } from "../village/VillageScene";
import { BLOCK_R, S, toWorld, type BuildCtx, type Spot } from "./common";
import * as M from "./models";

/** 十字路的路口（繞路用） */
export const TOWN_NODES = [[-0.36, -0.36], [-7, -0.36], [7, -0.36], [-13, -0.36], [13, -0.36], [-0.36, -6], [-0.36, 6]].map(
  ([x, z]) => new THREE.Vector3(x, 0.18, z),
);

/** 蓋村子：十字路（橫 roadW、直 roadH 長）＋廣場、裝飾、池塘、路燈 */
export function buildTown(ctx: BuildCtx, roadW: number, roadH: number) {
  const road = 0xd9bf8a;
  ctx.scene.add(M.flat(new THREE.PlaneGeometry(roadW, 0.75), road, 0, -0.36));
  ctx.scene.add(M.flat(new THREE.PlaneGeometry(0.75, roadH), road, -0.36, 0));
  ctx.scene.add(M.flat(new THREE.CircleGeometry(2.1, 24), road, 0, -0.36, 0.2));

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
    ctx.scene.add(obj);
    if (BLOCK_R[d.kind]) ctx.addObstacle(p.x, p.z, BLOCK_R[d.kind]);
  }
  const pondC = toWorld((POND.x0 + POND.x1 + 1) / 2, (POND.y0 + POND.y1 + 1) / 2);
  const pond = M.pond((POND.x1 - POND.x0 + 1) * S, (POND.y1 - POND.y0 + 1) * S);
  pond.position.set(pondC.x, 0, pondC.z);
  ctx.scene.add(pond);
  ctx.addObstacle(pondC.x, pondC.z, 2.1);

  // 路燈：廣場四角與小路旁（晚上亮）
  for (const [x, z] of [[2.6, 2.0], [-2.9, 2.0], [2.6, -2.8], [-2.9, -2.8], [9, 0.6], [-9, 0.6], [0.6, 8], [0.6, -8.5]]) ctx.addLamp(x, z);
}

/** 生活作息的地點：井邊、池塘邊、樹下、告示牌前、花圃、工作台、廣場、房子門口（位置跟著裝飾算） */
export function townSpots(ctx: BuildCtx, inside: (p: THREE.Vector3, margin: number) => boolean): Spot[] {
  const spots: Spot[] = [];
  const per = new Map<THREE.Vector3, number>(); // 每個東西旁邊最多幾個位子
  const add = (kind: SpotKind, pos: THREE.Vector3, face: THREE.Vector3, anchor: THREE.Vector3, max: number) => {
    if ((per.get(anchor) ?? 0) >= max) return;
    if (!inside(pos, 1.8) || ctx.blocked(pos.x, pos.z)) return;
    if (spots.some((x) => x.pos.distanceTo(pos) < 0.9)) return; // 不要擠在一起
    spots.push({ kind, pos: pos.setY(0.18), face, taken: null });
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
  return spots;
}
