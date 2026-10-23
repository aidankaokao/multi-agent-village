/**
 * 島嶼版的 3D 模型：全部用程式拿方塊、圓柱、圓球組成（低多邊形），不用任何模型檔或貼圖。
 * 村民的造型依像素版的外觀（sprite id）決定：衣服顏色跟 scripts/gen_placeholder_sprites.py 一樣，再加上各自的配件。
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";

const mats = new Map<string, THREE.MeshStandardMaterial>();

/** 共用材質（裝飾用）；村民的材質各自一份（外出中要變半透明） */
export function mat(color: number | string, rough = 0.85): THREE.MeshStandardMaterial {
  const key = `${color}-${rough}`;
  let m = mats.get(key);
  if (!m) {
    m = new THREE.MeshStandardMaterial({ color, roughness: rough, metalness: 0, flatShading: true });
    mats.set(key, m);
  }
  return m;
}

export function mesh(geo: THREE.BufferGeometry, material: THREE.Material, x = 0, y = 0, z = 0): THREE.Mesh {
  const m = new THREE.Mesh(geo, material);
  m.position.set(x, y, z);
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

// ---------------------------------------------------------------- 島

/** 平頂的六邊形頂點（半徑 r） */
export function hexPoints(r: number): THREE.Vector2[] {
  return Array.from({ length: 6 }, (_, i) => {
    const a = (Math.PI / 3) * i;
    return new THREE.Vector2(Math.cos(a) * r, Math.sin(a) * r);
  });
}

/** 圓角六邊形的島：草地頂、沙灘邊、土色側面 */
export function island(r: number, height = 1.2, grass = 0x8fc46a): THREE.Group {
  const g = new THREE.Group();
  const shape = (rr: number) => {
    const pts = hexPoints(rr);
    const s = new THREE.Shape();
    const round = rr * 0.18;
    pts.forEach((p, i) => {
      const prev = pts[(i + 5) % 6];
      const next = pts[(i + 1) % 6];
      const a = p.clone().add(prev.clone().sub(p).setLength(round));
      const b = p.clone().add(next.clone().sub(p).setLength(round));
      if (i === 0) s.moveTo(a.x, a.y);
      else s.lineTo(a.x, a.y);
      s.quadraticCurveTo(p.x, p.y, b.x, b.y);
    });
    s.closePath();
    return s;
  };
  const extrude = (rr: number, depth: number, color: number, y: number) => {
    const geo = new THREE.ExtrudeGeometry(shape(rr), { depth, bevelEnabled: false, curveSegments: 4 });
    geo.rotateX(Math.PI / 2); // 往下長出厚度
    const m = mesh(geo, mat(color), 0, y, 0);
    m.castShadow = false;
    return m;
  };
  g.add(extrude(r + 0.9, 0.35, 0xe9d8a6, -0.25)); // 沙灘
  g.add(extrude(r, height, 0x9a7a55, 0)); // 土
  const top = extrude(r, 0.12, grass, 0.06); // 草地
  g.add(top);
  return g;
}

export function sea(radius: number): THREE.Mesh {
  const m = new THREE.Mesh(new THREE.CircleGeometry(radius, 64), new THREE.MeshStandardMaterial({ color: 0x7ec7d0, roughness: 0.35 }));
  m.rotation.x = -Math.PI / 2;
  m.position.y = -0.55;
  m.receiveShadow = true;
  return m;
}

/** 地上的平面（小路、廣場）：稍微浮在草地上 */
export function flat(geo: THREE.BufferGeometry, color: number, x: number, z: number, y = 0.19): THREE.Mesh {
  geo.rotateX(-Math.PI / 2);
  const m = new THREE.Mesh(geo, mat(color, 1));
  m.position.set(x, y, z);
  m.receiveShadow = true;
  return m;
}

// ---------------------------------------------------------------- 裝飾

export function tree(scale = 1, seed = 0): THREE.Group {
  const g = new THREE.Group();
  g.add(mesh(new THREE.CylinderGeometry(0.12, 0.16, 0.8, 6), mat(0x8a5a36), 0, 0.4, 0));
  const greens = [0x5fa84d, 0x6db35a, 0x4f9a46];
  g.add(mesh(new THREE.IcosahedronGeometry(0.7, 0), mat(greens[seed % 3]), 0, 1.25, 0));
  g.add(mesh(new THREE.IcosahedronGeometry(0.45, 0), mat(greens[(seed + 1) % 3]), 0.3, 1.65, 0.1));
  g.scale.setScalar(scale);
  return g;
}

export function pine(scale = 1): THREE.Group {
  const g = new THREE.Group();
  g.add(mesh(new THREE.CylinderGeometry(0.1, 0.14, 0.5, 6), mat(0x7a4e2e), 0, 0.25, 0));
  [[0.75, 0.9, 0.85], [0.58, 0.75, 1.35], [0.38, 0.6, 1.8]].forEach(([r, h, y]) =>
    g.add(mesh(new THREE.ConeGeometry(r, h, 7), mat(0x3f8a48), 0, y, 0)),
  );
  g.scale.setScalar(scale);
  return g;
}

export function bush(): THREE.Group {
  const g = new THREE.Group();
  [[-0.3, 0.32], [0.25, 0.38], [0, 0.45]].forEach(([x, r], i) =>
    g.add(mesh(new THREE.IcosahedronGeometry(r, 0), mat(i % 2 ? 0x5a9e4a : 0x6aae55), x, r * 0.8, i === 2 ? -0.15 : 0.1)),
  );
  g.add(mesh(new THREE.SphereGeometry(0.06, 6, 4), mat(0xe2546a), 0.2, 0.6, 0.3));
  return g;
}

export function rock(): THREE.Group {
  const g = new THREE.Group();
  g.add(mesh(new THREE.DodecahedronGeometry(0.45, 0), mat(0xa9a59b), 0, 0.3, 0));
  g.add(mesh(new THREE.DodecahedronGeometry(0.25, 0), mat(0x8f8b82), 0.4, 0.18, 0.2));
  return g;
}

export function flowers(): THREE.Group {
  const g = new THREE.Group();
  const colors = [0xf2c230, 0xffffff, 0xe86a92, 0xb07ce8];
  for (let i = 0; i < 7; i++) {
    const a = i * 2.4;
    const r = 0.15 + (i % 3) * 0.18;
    g.add(mesh(new THREE.SphereGeometry(0.07, 6, 4), mat(colors[i % 4]), Math.cos(a) * r, 0.28, Math.sin(a) * r));
  }
  return g;
}

/** 房子：牆、斜屋頂、門窗（窗戶的材質之後夜晚會亮燈） */
export function house(wall = 0xf3e6cc, roof = 0xd0563f): THREE.Group {
  const g = new THREE.Group();
  g.add(mesh(new RoundedBoxGeometry(2.6, 1.6, 2.0, 2, 0.06), mat(wall), 0, 0.98, 0));
  const roofGeo = new THREE.CylinderGeometry(1.35, 1.35, 2.9, 3, 1);
  roofGeo.rotateZ(Math.PI / 2);
  roofGeo.rotateX(Math.PI / 6 + Math.PI);
  const r = mesh(roofGeo, mat(roof), 0, 2.15, 0);
  r.scale.set(1.05, 0.62, 0.95);
  g.add(r);
  g.add(mesh(new THREE.BoxGeometry(0.5, 0.85, 0.08), mat(0x7a4e2e), 0, 0.6, 1.02));
  const win = new THREE.MeshStandardMaterial({ color: 0xbfe0f2, emissive: 0xffc46b, emissiveIntensity: 0, flatShading: true });
  win.name = "window";
  g.add(mesh(new THREE.BoxGeometry(0.5, 0.45, 0.06), win, -0.85, 1.15, 1.02));
  g.add(mesh(new THREE.BoxGeometry(0.5, 0.45, 0.06), win, 0.85, 1.15, 1.02));
  g.add(mesh(new THREE.BoxGeometry(0.28, 0.6, 0.28), mat(0x8a6a5a), 0.8, 2.6, -0.3)); // 煙囪
  return g;
}

export function well(): THREE.Group {
  const g = new THREE.Group();
  g.add(mesh(new THREE.CylinderGeometry(0.6, 0.65, 0.6, 10), mat(0xa9a59b), 0, 0.42, 0));
  g.add(mesh(new THREE.CylinderGeometry(0.48, 0.48, 0.05, 10), mat(0x4f86d8, 0.3), 0, 0.7, 0));
  [-0.5, 0.5].forEach((x) => g.add(mesh(new THREE.BoxGeometry(0.1, 1.0, 0.1), mat(0x7a4e2e), x, 1.1, 0)));
  const roof = mesh(new THREE.ConeGeometry(0.85, 0.5, 4), mat(0xb6463a), 0, 1.75, 0);
  roof.rotation.y = Math.PI / 4;
  g.add(roof);
  return g;
}

export function sign(): THREE.Group {
  const g = new THREE.Group();
  g.add(mesh(new THREE.BoxGeometry(0.1, 0.9, 0.1), mat(0x7a4e2e), 0, 0.55, 0));
  g.add(mesh(new THREE.BoxGeometry(1.0, 0.45, 0.08), mat(0xc89a5a), 0, 0.95, 0));
  return g;
}

export function workbench(): THREE.Group {
  const g = new THREE.Group();
  g.add(mesh(new THREE.BoxGeometry(1.4, 0.12, 0.7), mat(0xa8763e), 0, 0.7, 0));
  [[-0.6, -0.28], [0.6, -0.28], [-0.6, 0.28], [0.6, 0.28]].forEach(([x, z]) =>
    g.add(mesh(new THREE.BoxGeometry(0.1, 0.66, 0.1), mat(0x7a4e2e), x, 0.35, z)),
  );
  g.add(mesh(new THREE.BoxGeometry(0.4, 0.2, 0.25), mat(0x6a6f7a), -0.3, 0.86, 0));
  return g;
}

export function pond(w: number, d: number): THREE.Group {
  const g = new THREE.Group();
  const rim = new THREE.CircleGeometry(1, 24);
  const water = new THREE.CircleGeometry(1, 24);
  const r = flat(rim, 0xb8a479, 0, 0, 0.17);
  r.scale.set(w / 2 + 0.25, 1, d / 2 + 0.25);
  const wtr = new THREE.Mesh(water, new THREE.MeshStandardMaterial({ color: 0x5c9fd8, roughness: 0.2 }));
  wtr.geometry.rotateX(-Math.PI / 2);
  wtr.position.y = 0.2;
  wtr.scale.set(w / 2, 1, d / 2);
  g.add(r, wtr);
  g.add(mesh(new THREE.CylinderGeometry(0.22, 0.22, 0.02, 8), mat(0x3f9a48), w * 0.22, 0.22, -d * 0.18));
  g.add(mesh(new THREE.CylinderGeometry(0.17, 0.17, 0.02, 8), mat(0x3f9a48), -w * 0.25, 0.22, d * 0.15));
  return g;
}

/** 木棧橋：從 a 到 b（世界座標，y 取島面高度） */
export function bridge(a: THREE.Vector3, b: THREE.Vector3): THREE.Group {
  const g = new THREE.Group();
  const len = a.distanceTo(b);
  const planks = Math.max(2, Math.round(len / 0.45));
  for (let i = 0; i < planks; i++) {
    const t = (i + 0.5) / planks;
    const p = a.clone().lerp(b, t);
    const plank = mesh(new THREE.BoxGeometry(1.3, 0.1, 0.36), mat(i % 2 ? 0xb88a52 : 0xa8784a), p.x, 0.05, p.z);
    plank.lookAt(b.x, 0.05, b.z);
    g.add(plank);
  }
  for (let i = 0; i <= planks; i += 2) {
    const p = a.clone().lerp(b, i / planks);
    const side = new THREE.Vector3(b.z - a.z, 0, a.x - b.x).setLength(0.62);
    [1, -1].forEach((s) => g.add(mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.9, 5), mat(0x7a4e2e), p.x + side.x * s, -0.1, p.z + side.z * s)));
  }
  return g;
}

export function boat(color = 0xd0563f): THREE.Group {
  const g = new THREE.Group();
  g.add(mesh(new THREE.BoxGeometry(0.5, 0.25, 1.3), mat(color), 0, 0.1, 0));
  g.add(mesh(new THREE.CylinderGeometry(0.03, 0.03, 1.2, 4), mat(0x7a4e2e), 0, 0.75, 0));
  const sail = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0.25, 0), new THREE.Vector3(0, 1.3, 0), new THREE.Vector3(0, 0.25, 0.7)]);
  sail.computeVertexNormals();
  g.add(mesh(sail, new THREE.MeshStandardMaterial({ color: 0xfaf6ee, side: THREE.DoubleSide, flatShading: true }), 0.04, 0, -0.2));
  return g;
}

/** 路燈：晚上燈罩會亮（材質名 lamp），旁邊再放一盞不投影的點光源 */
export function lamp(): THREE.Group {
  const g = new THREE.Group();
  g.add(mesh(new THREE.CylinderGeometry(0.05, 0.07, 1.7, 6), mat(0x3a3a44), 0, 0.85, 0));
  const glow = new THREE.MeshStandardMaterial({ color: 0xfff3d0, emissive: 0xffc46b, emissiveIntensity: 0, flatShading: true });
  glow.name = "lamp";
  g.add(mesh(new THREE.SphereGeometry(0.17, 10, 8), glow, 0, 1.8, 0));
  g.add(mesh(new THREE.ConeGeometry(0.22, 0.14, 6), mat(0x3a3a44), 0, 1.98, 0));
  return g;
}

export function cloud(): THREE.Group {
  const g = new THREE.Group();
  const white = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1, flatShading: true, transparent: true, opacity: 0.92 });
  [[0, 0, 1.2], [1.3, -0.2, 0.9], [-1.2, -0.25, 0.85], [0.5, 0.5, 0.8]].forEach(([x, y, r]) => {
    const m = new THREE.Mesh(new THREE.IcosahedronGeometry(r, 1), white);
    m.position.set(x, y, 0);
    g.add(m);
  });
  return g;
}

export function hill(r: number, h: number, color: number): THREE.Mesh {
  const m = mesh(new THREE.ConeGeometry(r, h, 6), mat(color), 0, h / 2 - 0.6, 0);
  m.castShadow = false;
  return m;
}

// ---------------------------------------------------------------- 村民（Q 版小人）

interface Look {
  hair: number;
  cloth: number;
  cloth2: number;
  acc?: (g: THREE.Group, m: (c: number) => THREE.MeshStandardMaterial) => void;
}

const SKIN = 0xf6cda6;

// 衣服顏色和 scripts/gen_placeholder_sprites.py 的 CHARACTERS 一樣，再加上 3D 配件
export const LOOKS: Record<string, Look> = {
  poet: { hair: 0x5a3a22, cloth: 0x3f8f5a, cloth2: 0x2c6640, acc: (g, m) => {
    g.add(mesh(new THREE.CylinderGeometry(0.34, 0.36, 0.1, 10), m(0x6a3a8a), 0.04, 1.27, 0)); // 貝雷帽
    g.add(mesh(new THREE.ConeGeometry(0.03, 0.4, 4), m(0xffffff), 0.32, 0.7, 0.18)); // 羽毛筆
  } },
  jester: { hair: 0xe08a2c, cloth: 0xd8343c, cloth2: 0x7a2a8a, acc: (g, m) => {
    [-1, 1].forEach((s) => {
      const c = mesh(new THREE.ConeGeometry(0.16, 0.5, 6), m(s < 0 ? 0xd8343c : 0x7a2a8a), s * 0.17, 1.38, 0);
      c.rotation.z = -s * 0.6;
      g.add(c);
      g.add(mesh(new THREE.SphereGeometry(0.07, 6, 4), m(0xf2c230), s * 0.38, 1.55, 0));
    });
  } },
  translator: { hair: 0x1e1e2a, cloth: 0x3a6fc4, cloth2: 0x28477d, acc: (g, m) => {
    [-0.11, 0.11].forEach((x) => g.add(mesh(new THREE.TorusGeometry(0.07, 0.015, 4, 10), m(0x222222), x, 0.98, 0.29)));
    g.add(mesh(new THREE.BoxGeometry(0.26, 0.32, 0.08), m(0x8a3a2a), 0.3, 0.5, 0.12)); // 書
  } },
  reviewer: { hair: 0x3b2a1e, cloth: 0x2b3a67, cloth2: 0x1b2442, acc: (g, m) => {
    g.add(mesh(new THREE.CylinderGeometry(0.3, 0.32, 0.16, 10), m(0x1b2442), 0, 1.25, 0)); // 制服帽
    g.add(mesh(new THREE.BoxGeometry(0.4, 0.04, 0.2), m(0x111111), 0, 1.18, 0.25));
    g.add(mesh(new THREE.SphereGeometry(0.05, 6, 4), m(0xe0b84a), 0.12, 0.62, 0.19)); // 徽章
  } },
  scholar: { hair: 0x4a3426, cloth: 0x5a4a8a, cloth2: 0x3a2f5e, acc: (g, m) => {
    g.add(mesh(new THREE.BoxGeometry(0.62, 0.05, 0.62), m(0x222233), 0, 1.3, 0)); // 學士帽
    g.add(mesh(new THREE.CylinderGeometry(0.22, 0.24, 0.12, 8), m(0x222233), 0, 1.22, 0));
    g.add(mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.4, 6), m(0xf1e6c8), 0.32, 0.5, 0.1)); // 卷軸
  } },
  merchant: { hair: 0x2a1e14, cloth: 0x2f7f7a, cloth2: 0x1f5552, acc: (g, m) => {
    g.add(mesh(new THREE.CylinderGeometry(0.5, 0.5, 0.04, 12), m(0x6a4a2a), 0, 1.2, 0)); // 寬簷帽
    g.add(mesh(new THREE.CylinderGeometry(0.25, 0.28, 0.2, 10), m(0x6a4a2a), 0, 1.3, 0));
    g.add(mesh(new THREE.SphereGeometry(0.14, 8, 6), m(0xc89a3a), 0.32, 0.38, 0.1)); // 錢袋
  } },
  farmer: { hair: 0x5a3a22, cloth: 0x7a9a3a, cloth2: 0x4a6a2a, acc: (g, m) => {
    g.add(mesh(new THREE.ConeGeometry(0.55, 0.28, 12), m(0xe6c46a), 0, 1.3, 0)); // 草帽
  } },
  chef: { hair: 0x3a2a1e, cloth: 0xe8e8ee, cloth2: 0x3a3a4a, acc: (g, m) => {
    g.add(mesh(new THREE.CylinderGeometry(0.24, 0.2, 0.42, 10), m(0xffffff), 0, 1.42, 0)); // 高帽
    g.add(mesh(new THREE.SphereGeometry(0.28, 10, 6), m(0xffffff), 0, 1.66, 0));
  } },
  painter: { hair: 0x2a1e14, cloth: 0xe8e2d0, cloth2: 0x9a8a6a, acc: (g, m) => {
    const b = mesh(new THREE.CylinderGeometry(0.34, 0.36, 0.1, 10), m(0xc8343c), 0.06, 1.27, 0); // 紅色貝雷帽
    b.rotation.z = -0.2;
    g.add(b);
    g.add(mesh(new THREE.CylinderGeometry(0.2, 0.2, 0.04, 8), m(0xc89a5a), 0.32, 0.5, 0.18)); // 調色盤
  } },
  typist: { hair: 0x2a1e14, cloth: 0xe0a43a, cloth2: 0xa8742a, acc: (g, m) => {
    const band = mesh(new THREE.TorusGeometry(0.33, 0.035, 4, 12, Math.PI), m(0x2a2a33), 0, 0.98, 0); // 耳機
    g.add(band);
    [-1, 1].forEach((s) => g.add(mesh(new THREE.CylinderGeometry(0.1, 0.1, 0.08, 10), m(0x3a8fd0), s * 0.33, 0.96, 0).rotateZ(Math.PI / 2)));
    g.add(mesh(new THREE.BoxGeometry(0.44, 0.03, 0.3), m(0x5a5f6a), 0, 0.55, 0.32)); // 筆電
    const scr = mesh(new THREE.BoxGeometry(0.44, 0.28, 0.03), m(0xbfe6ff), 0, 0.69, 0.18);
    scr.rotation.x = -0.25;
    g.add(scr);
  } },
  mayor: { hair: 0xc8c8c8, cloth: 0xa8323e, cloth2: 0x6e1f28, acc: (g, m) => {
    g.add(mesh(new THREE.CylinderGeometry(0.24, 0.26, 0.14, 8), m(0xe0b84a), 0, 1.3, 0)); // 王冠
    for (let i = 0; i < 5; i++) {
      const a = (i / 5) * Math.PI * 2;
      g.add(mesh(new THREE.ConeGeometry(0.05, 0.14, 4), m(0xe0b84a), Math.cos(a) * 0.22, 1.43, Math.sin(a) * 0.22));
    }
    g.add(mesh(new THREE.CylinderGeometry(0.03, 0.03, 1.1, 5), m(0x7a4e2e), 0.38, 0.55, 0.05)); // 法杖
    g.add(mesh(new THREE.SphereGeometry(0.08, 8, 6), m(0x6ad0f0), 0.38, 1.12, 0.05));
  } },
  craftsman: { hair: 0x3a2a1e, cloth: 0x4a6a8a, cloth2: 0x2a3a4a, acc: (g, m) => {
    g.add(mesh(new THREE.SphereGeometry(0.34, 10, 6, 0, Math.PI * 2, 0, Math.PI / 2), m(0xf2c230), 0, 1.07, 0)); // 安全帽
    g.add(mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.5, 5), m(0x7a4e2e), 0.34, 0.5, 0.12)); // 槌子
    g.add(mesh(new THREE.BoxGeometry(0.2, 0.1, 0.1), m(0x6a6f7a), 0.34, 0.75, 0.12));
  } },
  scribe: { hair: 0x3a2a1e, cloth: 0x8a6a3a, cloth2: 0x5a4424, acc: (g, m) => {
    g.add(mesh(new THREE.SphereGeometry(0.34, 10, 6, 0, Math.PI * 2, 0, Math.PI / 2.2), m(0x5a3a22), 0, 1.0, -0.02)); // 頭巾
    g.add(mesh(new THREE.BoxGeometry(0.3, 0.22, 0.06), m(0x7a3a2a), 0.05, 0.5, 0.22)); // 筆記本
  } },
  appraiser: { hair: 0xd8d0c0, cloth: 0x6a3a7a, cloth2: 0x46264f, acc: (g, m) => {
    g.add(mesh(new THREE.CylinderGeometry(0.42, 0.42, 0.03, 12), m(0x2a3a2e), 0, 1.2, 0)); // 圓頂禮帽
    g.add(mesh(new THREE.SphereGeometry(0.25, 10, 6, 0, Math.PI * 2, 0, Math.PI / 2), m(0x2a3a2e), 0, 1.2, 0));
    g.add(mesh(new THREE.TorusGeometry(0.07, 0.015, 4, 10), m(0xe0b84a), 0.11, 0.98, 0.29)); // 單片眼鏡
    g.add(mesh(new THREE.BoxGeometry(0.36, 0.28, 0.05), m(0xe0b84a), 0.05, 0.5, 0.24)); // 畫框
  } },
  auditor: { hair: 0x5a4a3a, cloth: 0x2a2a3a, cloth2: 0x1a1a26, acc: (g, m) => {
    g.add(mesh(new THREE.TorusGeometry(0.1, 0.025, 4, 12), m(0xc8c8c8), 0.32, 0.72, 0.2)); // 放大鏡
    g.add(mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.22, 4), m(0x7a4e2e), 0.32, 0.55, 0.2));
  } },
};

export interface Chibi {
  root: THREE.Group;
  body: THREE.Group; // 整個身體（坐下時往下移、打瞌睡時往前傾）
  legs: THREE.Group[]; // 髖關節
  arms: THREE.Group[]; // 肩關節
  materials: THREE.MeshStandardMaterial[];
}

/** Q 版小人：大頭、圓身體、短手短腳（約 1.4 單位高），面向 +z */
export function chibi(sprite: string): Chibi {
  const look = LOOKS[sprite] ?? LOOKS.poet;
  const materials: THREE.MeshStandardMaterial[] = [];
  const m = (color: number) => {
    const x = new THREE.MeshStandardMaterial({ color, roughness: 0.8, flatShading: false });
    materials.push(x);
    return x;
  };
  const root = new THREE.Group();
  const body = new THREE.Group();
  root.add(body);
  // 手腳掛在關節（髖、肩）上，坐下、舉手時才會從關節轉
  const legs = [-0.12, 0.12].map((x) => {
    const hip = new THREE.Group();
    hip.position.set(x, 0.26, 0);
    hip.add(mesh(new RoundedBoxGeometry(0.15, 0.26, 0.16, 2, 0.05), m(look.cloth2), 0, -0.13, 0));
    body.add(hip);
    return hip;
  });
  body.add(mesh(new RoundedBoxGeometry(0.52, 0.48, 0.38, 3, 0.14), m(look.cloth), 0, 0.5, 0));
  const arms = [-1, 1].map((s) => {
    const shoulder = new THREE.Group();
    shoulder.position.set(s * 0.29, 0.66, 0);
    shoulder.rotation.z = s * 0.25;
    shoulder.add(mesh(new THREE.CapsuleGeometry(0.07, 0.22, 3, 6), m(look.cloth), 0, -0.14, 0));
    body.add(shoulder);
    return shoulder;
  });
  body.add(mesh(new THREE.SphereGeometry(0.34, 16, 12), m(SKIN), 0, 0.98, 0)); // 頭
  const hair = mesh(new THREE.SphereGeometry(0.36, 16, 10, 0, Math.PI * 2, 0, Math.PI / 2.1), m(look.hair), 0, 1.0, -0.03);
  hair.rotation.x = -0.25;
  body.add(hair);
  [-0.11, 0.11].forEach((x) => body.add(mesh(new THREE.SphereGeometry(0.04, 8, 6), m(0x2a2028), x, 0.96, 0.31))); // 眼睛
  [-0.19, 0.19].forEach((x) => body.add(mesh(new THREE.SphereGeometry(0.045, 8, 6), m(0xf0a0a0), x, 0.88, 0.27))); // 腮紅
  look.acc?.(body, m);
  return { root, body, legs, arms, materials };
}

// ---------------------------------------------------------------- 生活用的小道具（掛在村民身上，村民自己的座標，面向 +z）

/** 做事時拿的東西；easel 這類放在地上的也掛在村民身上（跟著村民走，收起來時一起拿掉） */
export function prop(kind: string): THREE.Group {
  const g = new THREE.Group();
  const add = (geo: THREE.BufferGeometry, color: number, x: number, y: number, z: number) => {
    const p = mesh(geo, mat(color, 0.7), x, y, z);
    g.add(p);
    return p;
  };
  switch (kind) {
    case "rod": {
      const r = add(new THREE.CylinderGeometry(0.018, 0.025, 1.5, 5), 0x7a4e2e, 0.22, 0.75, 0.6);
      r.rotation.x = 1.05;
      add(new THREE.CylinderGeometry(0.004, 0.004, 0.75, 3), 0xeeeeee, 0.22, 0.75, 1.27); // 釣線
      add(new THREE.SphereGeometry(0.05, 6, 4), 0xd0563f, 0.22, 0.38, 1.27); // 浮標
      break;
    }
    case "book":
      add(new THREE.BoxGeometry(0.34, 0.24, 0.05), 0x8a3a2a, 0, 0.62, 0.34).rotation.x = -0.5;
      add(new THREE.BoxGeometry(0.32, 0.22, 0.02), 0xf6efe0, 0, 0.64, 0.36).rotation.x = -0.5;
      break;
    case "notebook":
      add(new THREE.BoxGeometry(0.3, 0.02, 0.22), 0xf6efe0, -0.03, 0.42, 0.32);
      add(new THREE.CylinderGeometry(0.012, 0.012, 0.2, 4), 0x2a2028, 0.12, 0.48, 0.32).rotation.z = 0.8;
      break;
    case "paper":
      add(new THREE.BoxGeometry(0.36, 0.28, 0.015), 0xf3ead3, 0, 0.66, 0.36).rotation.x = -0.35;
      break;
    case "scroll":
      add(new THREE.BoxGeometry(0.42, 0.26, 0.01), 0xf1e6c8, 0, 0.64, 0.36).rotation.x = -0.4;
      [-1, 1].forEach((s) => (add(new THREE.CylinderGeometry(0.03, 0.03, 0.3, 6), 0x8a5a2a, s * 0.22, 0.64, 0.36).rotation.x = -0.4));
      break;
    case "can":
      add(new THREE.CylinderGeometry(0.1, 0.12, 0.18, 8), 0x3a8fd0, 0.3, 0.42, 0.32);
      add(new THREE.CylinderGeometry(0.015, 0.025, 0.22, 4), 0x3a8fd0, 0.3, 0.46, 0.48).rotation.x = 1.0;
      break;
    case "riceball":
      add(new THREE.ConeGeometry(0.1, 0.14, 3), 0xffffff, 0.2, 0.68, 0.26);
      add(new THREE.BoxGeometry(0.1, 0.05, 0.02), 0x1e2a1e, 0.2, 0.63, 0.31);
      break;
    case "broom": {
      const b = add(new THREE.CylinderGeometry(0.02, 0.02, 0.9, 5), 0x9a6a3a, 0.24, 0.45, 0.3);
      b.rotation.x = 0.4;
      add(new THREE.ConeGeometry(0.12, 0.22, 6), 0xd8b45a, 0.24, 0.06, 0.46);
      break;
    }
    case "telescope": {
      const t = add(new THREE.CylinderGeometry(0.05, 0.07, 0.5, 8), 0xc89a3a, 0.08, 1.0, 0.42);
      t.rotation.x = 1.25;
      break;
    }
    case "easel": {
      [-1, 1].forEach((s) => (add(new THREE.CylinderGeometry(0.02, 0.02, 0.95, 4), 0x8a5a2a, s * 0.2, 0.45, 0.62).rotation.z = s * 0.12));
      add(new THREE.BoxGeometry(0.5, 0.42, 0.03), 0xffffff, 0, 0.72, 0.6);
      add(new THREE.BoxGeometry(0.16, 0.12, 0.01), 0xe8643c, -0.08, 0.76, 0.58);
      add(new THREE.BoxGeometry(0.14, 0.1, 0.01), 0x3a8fd0, 0.1, 0.66, 0.58);
      break;
    }
    case "cup":
      add(new THREE.CylinderGeometry(0.06, 0.05, 0.1, 8), 0xffffff, 0.24, 0.5, 0.22);
      break;
    case "abacus":
      add(new THREE.BoxGeometry(0.4, 0.03, 0.22), 0x7a4e2e, 0, 0.42, 0.34);
      for (let i = 0; i < 4; i++) add(new THREE.BoxGeometry(0.34, 0.03, 0.03), 0xd0563f, 0, 0.45, 0.26 + i * 0.05);
      break;
  }
  return g;
}

export function disposeProp(g: THREE.Object3D) {
  g.removeFromParent();
  g.traverse((o) => {
    if (o instanceof THREE.Mesh) o.geometry.dispose(); // 材質是共用的，不釋放
  });
}
