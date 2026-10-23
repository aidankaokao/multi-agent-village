/**
 * 鐵道小鎮的模型：鐵道、月台、車站（依駐村專家的樣式）、倉庫（中轉站）、小火車、平交道、田、看板。
 * 一樣全部用程式組（方塊、圓柱、圓球），不用模型檔；建築物的正面朝 +z（擺好後 lookAt 村子中心）。
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { mat, mesh } from "../world3d/models";

/** 晚上會亮的材質（World3D 依名字收集：window / lamp）；每棟各自一份，外出中才能單獨熄燈 */
function glow(kind: "window" | "lamp", color = 0xbfe0f2): THREE.MeshStandardMaterial {
  const m = new THREE.MeshStandardMaterial({ color: kind === "lamp" ? 0xfff3d0 : color, emissive: 0xffc46b, emissiveIntensity: 0, flatShading: true });
  m.name = kind;
  return m;
}

/** 寫字的看板（畫在 canvas 上當貼圖）；w × h 單位，只有正面（+z） */
export function textBoard(text: string, w: number, h: number, bg = "#f6efe0", fg = "#3a2a1e"): THREE.Mesh {
  const scale = 128;
  const c = document.createElement("canvas");
  c.width = Math.round(w * scale);
  c.height = Math.round(h * scale);
  const g = c.getContext("2d")!;
  g.fillStyle = bg;
  g.fillRect(0, 0, c.width, c.height);
  g.strokeStyle = fg;
  g.lineWidth = 6;
  g.strokeRect(5, 5, c.width - 10, c.height - 10);
  g.fillStyle = fg;
  g.textAlign = "center";
  g.textBaseline = "middle";
  let size = c.height * 0.55;
  g.font = `bold ${size}px "PingFang TC", "Noto Sans TC", sans-serif`;
  while (g.measureText(text).width > c.width * 0.88 && size > 10) {
    size -= 2;
    g.font = `bold ${size}px "PingFang TC", "Noto Sans TC", sans-serif`;
  }
  g.fillText(text, c.width / 2, c.height / 2 + 2);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshStandardMaterial({ map: tex, roughness: 0.9 }));
  m.castShadow = true;
  return m;
}

/** 立在兩根柱子上的看板（前後兩面都寫字，從哪邊看都是正的） */
export function signPost(text: string, w = 1.6, h = 0.42, bg?: string, fg?: string): THREE.Group {
  const g = new THREE.Group();
  [-1, 1].forEach((s) => g.add(mesh(new THREE.CylinderGeometry(0.04, 0.04, 1.2, 5), mat(0x5a4a3a), s * (w / 2 - 0.08), 0.6, 0)));
  for (const side of [0, Math.PI]) {
    const b = textBoard(text, w, h, bg, fg);
    b.position.set(0, 1.15, side ? -0.012 : 0.012);
    b.rotation.y = side;
    g.add(b);
  }
  return g;
}

/** 環狀鐵道：碎石道床、枕木、兩條鐵軌（半徑 r，中心在原點） */
export function track(r: number): THREE.Group {
  const g = new THREE.Group();
  const bed = new THREE.Mesh(new THREE.RingGeometry(r - 0.85, r + 0.85, 160, 1), mat(0xb8ad9a, 1));
  bed.rotation.x = -Math.PI / 2;
  bed.position.y = 0.2;
  bed.receiveShadow = true;
  g.add(bed);
  const n = Math.round((Math.PI * 2 * r) / 0.55);
  const sleepers = new THREE.InstancedMesh(new THREE.BoxGeometry(1.35, 0.08, 0.22), mat(0x7a5a3e), n);
  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), -a); // 枕木橫跨兩條鐵軌（沿半徑方向）
    m.compose(new THREE.Vector3(Math.cos(a) * r, 0.24, Math.sin(a) * r), q, new THREE.Vector3(1, 1, 1));
    sleepers.setMatrixAt(i, m);
  }
  sleepers.receiveShadow = true;
  g.add(sleepers);
  for (const d of [-0.42, 0.42]) {
    const rail = new THREE.Mesh(new THREE.TorusGeometry(r + d, 0.045, 4, 200), mat(0x8a8f98, 0.4));
    rail.rotation.x = Math.PI / 2;
    rail.position.y = 0.31;
    g.add(rail);
  }
  return g;
}

/** 月台：長 len（沿 x）、深 d */
export function platform(len: number, d = 1.3): THREE.Group {
  const g = new THREE.Group();
  g.add(mesh(new THREE.BoxGeometry(len, 0.36, d), mat(0xcfc6b4), 0, 0.36, 0));
  g.add(mesh(new THREE.BoxGeometry(len, 0.02, 0.12), mat(0xf2c230), 0, 0.55, d / 2 - 0.12)); // 黃線
  return g;
}

/** 有山牆屋頂的房子身體（正面 +z）：w × h × d */
function body(g: THREE.Group, w: number, h: number, d: number, wall: number, roof: number, roofH = 0.62) {
  g.add(mesh(new RoundedBoxGeometry(w, h, d, 2, 0.05), mat(wall), 0, 0.18 + h / 2, 0));
  const roofGeo = new THREE.CylinderGeometry(d * 0.68, d * 0.68, w + 0.3, 3, 1);
  roofGeo.rotateZ(Math.PI / 2);
  roofGeo.rotateX(Math.PI / 6 + Math.PI);
  const r = mesh(roofGeo, mat(roof), 0, 0.18 + h + 0.32, 0);
  r.scale.set(1, roofH, 0.95);
  g.add(r);
}

function windows(g: THREE.Group, xs: number[], y: number, z: number, m: THREE.Material, w = 0.42, h = 0.42) {
  for (const x of xs) g.add(mesh(new THREE.BoxGeometry(w, h, 0.06), m, x, y, z));
}

/** 背面也開一樣的窗（從鏡頭這邊常看到車站的背面） */
function mirrorWindows(g: THREE.Group, win: THREE.Material) {
  const front = g.children.filter((c) => c instanceof THREE.Mesh && c.material === win && c.position.z > 0.5);
  for (const c of front) {
    const b = c.clone();
    b.position.z = -c.position.z;
    g.add(b);
  }
}

function door(g: THREE.Group, z: number, color = 0x7a4e2e, w = 0.5) {
  g.add(mesh(new THREE.BoxGeometry(w, 0.8, 0.08), mat(color), 0, 0.58, z));
}

export type StationStyle = "brick" | "press" | "radio" | "archive" | "wood";

/** 依名字 / 技能挑車站的樣式 */
export function styleFor(text: string): StationStyle {
  if (/歷史|史/.test(text)) return "brick";
  if (/報社|報紙|日報/.test(text)) return "press";
  if (/研究|搜尋|網路|新聞/.test(text)) return "radio";
  if (/文件|彙整|報告|檔案/.test(text)) return "archive";
  return "wood";
}

/**
 * 駐村專家的小站（正面 +z）。userData.smoke：會冒煙的煙囪口（報社）
 */
export function station(style: StationStyle, hue = 0): THREE.Group {
  const g = new THREE.Group();
  const win = glow("window");
  if (style === "brick") {
    // 紅磚古蹟站：紅磚牆、白色飾帶、深灰四坡屋頂、小鐘樓
    g.add(mesh(new RoundedBoxGeometry(3.0, 1.5, 1.8, 2, 0.04), mat(0xa4553c), 0, 0.93, 0));
    g.add(mesh(new THREE.BoxGeometry(3.06, 0.1, 1.86), mat(0xf1e8d8), 0, 1.3, 0));
    const roof = mesh(new THREE.ConeGeometry(2.2, 0.9, 4), mat(0x4a4f5a), 0, 2.12, 0);
    roof.rotation.y = Math.PI / 4;
    roof.scale.set(1, 1, 0.62);
    g.add(roof);
    g.add(mesh(new THREE.BoxGeometry(0.6, 0.7, 0.6), mat(0xa4553c), 0, 2.6, 0.2));
    const cap = mesh(new THREE.ConeGeometry(0.5, 0.45, 4), mat(0x4a4f5a), 0, 3.15, 0.2);
    cap.rotation.y = Math.PI / 4;
    g.add(cap);
    g.add(mesh(new THREE.CylinderGeometry(0.17, 0.17, 0.04, 16), mat(0xf6efe0), 0, 2.65, 0.52).rotateX(Math.PI / 2));
    windows(g, [-1.0, 1.0], 0.95, 0.92, win, 0.38, 0.5);
    door(g, 0.92, 0x5a3424, 0.56);
  } else if (style === "press") {
    // 印刷廠站：灰色廠房、鋸齒屋頂、高煙囪（冒煙）
    g.add(mesh(new RoundedBoxGeometry(3.4, 1.5, 2.0, 2, 0.04), mat(0x9aa0a6), 0, 0.93, 0));
    for (let i = 0; i < 3; i++) {
      const t = mesh(new THREE.CylinderGeometry(0.7, 0.7, 2.0, 3, 1), mat(0x5a6470), -1.1 + i * 1.1, 1.85, 0);
      t.rotation.x = Math.PI / 2;
      t.rotation.y = Math.PI / 2;
      t.scale.set(0.8, 1, 0.55);
      g.add(t);
    }
    g.add(mesh(new THREE.CylinderGeometry(0.2, 0.26, 3.2, 10), mat(0x8a5a4a), 1.25, 1.8, -0.55));
    const smoke: THREE.Mesh[] = [];
    for (let i = 0; i < 4; i++) {
      const puff = new THREE.Mesh(new THREE.SphereGeometry(0.22, 8, 6), new THREE.MeshStandardMaterial({ color: 0xeeeeee, transparent: true, opacity: 0.7, flatShading: true }));
      puff.position.set(1.25, 3.5, -0.55);
      puff.userData.phase = i / 4;
      g.add(puff);
      smoke.push(puff);
    }
    g.userData.smoke = smoke;
    windows(g, [-1.15, -0.4, 0.4, 1.15], 1.1, 1.02, win, 0.4, 0.36);
    door(g, 1.02, 0x3a4a5a, 0.7);
  } else if (style === "radio") {
    // 電台站：白牆藍頂的小屋、旁邊一座紅白天線塔（頂端紅燈晚上亮）
    g.add(mesh(new RoundedBoxGeometry(2.4, 1.3, 1.7, 2, 0.04), mat(0xeef0f2), 0, 0.83, 0));
    g.add(mesh(new THREE.BoxGeometry(2.6, 0.14, 1.9), mat(0x3a6fc4), 0, 1.55, 0));
    const tower = new THREE.Group();
    for (let i = 0; i < 6; i++) {
      tower.add(mesh(new THREE.CylinderGeometry(0.12 - i * 0.015, 0.14 - i * 0.015, 0.75, 4), mat(i % 2 ? 0xffffff : 0xd0563f), 0, 0.55 + i * 0.75, 0));
    }
    const beacon = mesh(new THREE.SphereGeometry(0.13, 8, 6), glow("lamp"), 0, 5.1, 0);
    (beacon.material as THREE.MeshStandardMaterial).emissive.setHex(0xff3a2a);
    tower.add(beacon);
    tower.position.set(-1.7, 0, -0.4);
    g.add(tower);
    g.add(mesh(new THREE.CylinderGeometry(0.4, 0.4, 0.05, 14), mat(0xc8c8c8), 0.6, 1.9, -0.2).rotateX(-0.6)); // 碟形天線
    windows(g, [-0.7, 0.7], 0.9, 0.87, win);
    door(g, 0.87, 0x3a6fc4);
  } else if (style === "archive") {
    // 檔案室站：米色平頂建築、女兒牆、一排小窗、門口堆著紙箱
    g.add(mesh(new RoundedBoxGeometry(3.0, 1.9, 1.8, 2, 0.04), mat(0xd9cfb8), 0, 1.13, 0));
    g.add(mesh(new THREE.BoxGeometry(3.1, 0.2, 1.9), mat(0x7a8a9a), 0, 2.16, 0));
    windows(g, [-1.05, -0.35, 0.35, 1.05], 1.6, 0.92, win, 0.36, 0.34);
    windows(g, [-1.05, 1.05], 0.85, 0.92, win, 0.36, 0.34);
    door(g, 0.92, 0x4a5a6a);
    [[0.75, 0.33, 1.3], [1.1, 0.33, 1.25], [0.92, 0.6, 1.28]].forEach(([x, y, z]) => g.add(mesh(new THREE.BoxGeometry(0.32, 0.26, 0.3), mat(0xc89a5a), x, y, z)));
  } else {
    // 木造小站：咖啡色木牆、綠色屋頂
    const roofs = [0x3f7f5a, 0x2f6f8a, 0x8a5a3a, 0x6a4a8a];
    body(g, 2.6, 1.3, 1.7, 0x9a6a42, roofs[hue % roofs.length]);
    windows(g, [-0.8, 0.8], 0.9, 0.87, win);
    door(g, 0.87, 0x5a3a22);
  }
  mirrorWindows(g, win);
  return g;
}

/** 主車站（正面 +z）：奶油色牆、紅屋頂、門上時鐘 */
export function mainStation(): THREE.Group {
  const g = new THREE.Group();
  body(g, 5.0, 1.8, 2.4, 0xf3e6cc, 0xc8503a, 0.7);
  const win = glow("window");
  windows(g, [-1.7, -0.9, 0.9, 1.7], 1.2, 1.22, win, 0.48, 0.6);
  mirrorWindows(g, win);
  door(g, 1.22, 0x7a4e2e, 0.9);
  g.add(mesh(new THREE.CylinderGeometry(0.28, 0.28, 0.06, 18), mat(0xffffff), 0, 1.8, 1.24).rotateX(Math.PI / 2)); // 時鐘
  g.add(mesh(new THREE.BoxGeometry(0.03, 0.2, 0.02), mat(0x222222), 0, 1.86, 1.28));
  g.add(mesh(new THREE.BoxGeometry(0.15, 0.03, 0.02), mat(0x222222), 0.06, 1.8, 1.28));
  return g;
}

/**
 * 倉庫＝中轉站（正面 +z，開放式）：屋頂、後牆、兩層貨架。userData.slots：放包裹的位置（R2 用）
 */
export function warehouse(): THREE.Group {
  const g = new THREE.Group();
  g.add(mesh(new THREE.BoxGeometry(3.2, 1.6, 0.12), mat(0xb08a5a), 0, 0.98, -0.9)); // 後牆
  [-1, 1].forEach((s) => g.add(mesh(new THREE.BoxGeometry(0.12, 1.6, 1.9), mat(0xb08a5a), s * 1.55, 0.98, 0)));
  const roof = mesh(new THREE.BoxGeometry(3.6, 0.12, 2.3), mat(0x5a6470), 0, 1.85, 0.05);
  roof.rotation.x = -0.12;
  g.add(roof);
  const slots: THREE.Vector3[] = [];
  for (const y of [0.55, 1.15]) {
    g.add(mesh(new THREE.BoxGeometry(2.8, 0.06, 0.6), mat(0x7a5a3e), 0, y, -0.55));
    for (let i = 0; i < 4; i++) slots.push(new THREE.Vector3(-1.05 + i * 0.7, y + 0.17, -0.55));
  }
  g.userData.slots = slots;
  // 先擺幾個空箱子，看得出是倉庫
  [[-1.05, 0.72], [0.35, 1.32]].forEach(([x, y]) => g.add(mesh(new THREE.BoxGeometry(0.4, 0.3, 0.36), mat(0xc89a5a), x, y, -0.55)));
  return g;
}

/** 小火車：火車頭＋兩節車廂，沿 +x 排（車頭在 +x） */
export function train(): THREE.Group {
  const g = new THREE.Group();
  const loco = new THREE.Group();
  loco.add(mesh(new RoundedBoxGeometry(1.0, 0.62, 0.78, 2, 0.08), mat(0x2e3a48), 0.3, 0.72, 0));
  loco.add(mesh(new THREE.CylinderGeometry(0.33, 0.33, 0.9, 12), mat(0xc8503a), 0.85, 0.75, 0).rotateZ(Math.PI / 2));
  loco.add(mesh(new THREE.CylinderGeometry(0.09, 0.12, 0.35, 8), mat(0x2e3a48), 1.05, 1.18, 0));
  loco.add(mesh(new RoundedBoxGeometry(0.6, 0.55, 0.82, 2, 0.06), mat(0xc8503a), -0.05, 1.2, 0));
  loco.add(mesh(new THREE.SphereGeometry(0.09, 8, 6), glow("lamp"), 1.32, 0.78, 0));
  g.add(loco);
  const win = glow("window", 0xd8ecf6);
  [0xe9d8a6, 0x3f7f5a].forEach((c, i) => {
    const car = new THREE.Group();
    car.add(mesh(new RoundedBoxGeometry(1.3, 0.62, 0.78, 2, 0.08), mat(c), 0, 0.82, 0));
    car.add(mesh(new THREE.BoxGeometry(1.36, 0.06, 0.84), mat(0x5a3a2a), 0, 1.16, 0));
    windows(car, [-0.35, 0.2], 0.9, 0.4, win, 0.3, 0.24);
    windows(car, [-0.35, 0.2], 0.9, -0.4, win, 0.3, 0.24);
    car.position.x = -1.2 - i * 1.45;
    g.add(car);
  });
  // 輪子
  for (const x of [1.0, 0.3, -0.85, -1.55, -2.3, -3.0]) {
    for (const z of [-0.36, 0.36]) g.add(mesh(new THREE.CylinderGeometry(0.17, 0.17, 0.08, 10), mat(0x222228), x, 0.42, z).rotateX(Math.PI / 2));
  }
  return g;
}

/** 平交道：鐵軌上鋪木板、兩邊黑黃柱子（沿 z 是路的方向） */
export function crossing(): THREE.Group {
  const g = new THREE.Group();
  g.add(mesh(new THREE.BoxGeometry(1.5, 0.06, 1.9), mat(0x8a6a4a), 0, 0.3, 0));
  [-1, 1].forEach((s) => {
    const post = new THREE.Group();
    for (let i = 0; i < 4; i++) post.add(mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.24, 6), mat(i % 2 ? 0x222222 : 0xf2c230), 0, 0.3 + i * 0.24, 0));
    post.add(mesh(new THREE.BoxGeometry(0.34, 0.34, 0.04), mat(0xffffff), 0, 1.3, 0).rotateZ(Math.PI / 4));
    post.position.set(s * 1.05, 0, s * 1.15);
    g.add(post);
  });
  return g;
}

/** 長椅（正面 +z）：座面很低，坐下的村民（Q 版、腳短）剛好坐在上面 */
export function bench(): THREE.Group {
  const g = new THREE.Group();
  g.add(mesh(new THREE.BoxGeometry(1.6, 0.07, 0.42), mat(0x9a6a42), 0, 0.27, 0));
  g.add(mesh(new THREE.BoxGeometry(1.6, 0.42, 0.06), mat(0x9a6a42), 0, 0.55, -0.22));
  [-0.65, 0.65].forEach((x) => g.add(mesh(new THREE.BoxGeometry(0.08, 0.2, 0.36), mat(0x3a3a44), x, 0.13, 0)));
  return g;
}

/** 一塊田：一排一排的作物（w × d，沿 x 排） */
export function field(w: number, d: number, crop: number, soil = 0x8a6a48): THREE.Group {
  const g = new THREE.Group();
  const base = new THREE.Mesh(new THREE.BoxGeometry(w, 0.06, d), mat(soil, 1));
  base.position.y = 0.2;
  base.receiveShadow = true;
  g.add(base);
  const rows = Math.max(2, Math.floor(d / 0.55));
  for (let i = 0; i < rows; i++) {
    const row = new THREE.Mesh(new THREE.BoxGeometry(w - 0.3, 0.18, 0.26), mat(crop));
    row.position.set(0, 0.3, -d / 2 + 0.3 + i * ((d - 0.6) / Math.max(1, rows - 1)));
    row.castShadow = true;
    g.add(row);
  }
  return g;
}
