/**
 * 效能：把不會動的場景零件（樹、田、房子、鐵道…）依材質合併成幾個大網格，每一幀要畫的次數少很多（畫面一樣）。
 *
 * 不合併的：標了 userData.dynamic 的物件（火車、雲…）和它底下的東西、晚上會亮的材質（window / lamp，
 * 要能各自熄燈）、半透明的、InstancedMesh（已經是一次畫完）。
 */
import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

export function bakeStatic(scene: THREE.Scene) {
  scene.updateMatrixWorld(true);
  const groups = new Map<string, { mat: THREE.Material; cast: boolean; receive: boolean; geos: THREE.BufferGeometry[]; meshes: THREE.Mesh[] }>();
  const visit = (o: THREE.Object3D) => {
    if (o.userData.dynamic) return;
    if (o instanceof THREE.Mesh && !(o instanceof THREE.InstancedMesh) && !Array.isArray(o.material)) {
      const m = o.material as THREE.Material;
      if (m.name !== "window" && m.name !== "lamp" && !m.transparent) {
        const key = `${m.uuid}|${o.castShadow}|${o.receiveShadow}`;
        let g = groups.get(key);
        if (!g) groups.set(key, (g = { mat: m, cast: o.castShadow, receive: o.receiveShadow, geos: [], meshes: [] }));
        let geo = o.geometry.clone().applyMatrix4(o.matrixWorld);
        if (geo.index) geo = geo.toNonIndexed();
        for (const name of Object.keys(geo.attributes)) if (!["position", "normal", "uv"].includes(name)) geo.deleteAttribute(name);
        if (!geo.attributes.uv) geo.setAttribute("uv", new THREE.Float32BufferAttribute(new Float32Array((geo.attributes.position.count) * 2), 2));
        geo.clearGroups();
        g.geos.push(geo);
        g.meshes.push(o);
      }
    }
    for (const c of [...o.children]) visit(c);
  };
  for (const c of [...scene.children]) visit(c);

  for (const g of groups.values()) {
    if (g.geos.length < 2) {
      g.geos.forEach((x) => x.dispose());
      continue;
    }
    const merged = mergeGeometries(g.geos, false);
    g.geos.forEach((x) => x.dispose());
    if (!merged) continue;
    for (const m of g.meshes) {
      m.removeFromParent();
      m.geometry.dispose(); // 材質還在用（合併後的網格共用同一個）
    }
    const mesh = new THREE.Mesh(merged, g.mat);
    mesh.castShadow = g.cast;
    mesh.receiveShadow = g.receive;
    scene.add(mesh);
  }
}
