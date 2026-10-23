/**
 * 村子的配置（和畫法無關）：30×20 格的地圖、裝飾與池塘的位置、障礙物、村民資料、系統角色。
 * 3D 地圖（world3d/town.ts 用 toWorld 換算）與村民編輯器（選住處的小地圖）共用。
 * 角色外觀（sprite id）對應 public/assets/sprites/manifest.json：村民管理的頭像用圖片，3D 依 id 換衣服顏色與配件。
 */
export interface SpriteManifest {
  frameWidth: number;
  frameHeight: number;
  rows: string[]; // ["down","left","right","up"]
  walkCycle: number[];
  characters: Record<string, { name: string; path: string; desc?: string; system?: boolean }>; // system：村長、工匠專用
  decor?: Record<string, { path: string; width: number; height: number }>;
  ui: { status_bubbles: { path: string; frameWidth: number; frameHeight: number } };
}

export const MAP_W = 30;
export const MAP_H = 20;
/** 村裡要出現的村民；來自後端 /api/agents（agent.yaml 的 x-village），村長等系統角色由前端固定加入 */
export interface VillagerInfo {
  id: string;
  name: string;
  sprite: string | null;
  home: [number, number] | null;
  away?: boolean; // 駐村專家連不上（外出中）：半透明、名字後面標示
  lines?: string[]; // 閒著時偶爾冒出的短句（從名片產生，10 個字左右）
  skill?: string; // 一句話的專長（村民互動時別人會提到），例如「寫詩」
  remote?: boolean; // 駐村專家（住在鐵道沿線自己的小站）
}

// ---------------------------------------------------------------- 地圖裝飾與障礙物

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

export const MAYOR: VillagerInfo = { id: "mayor", name: "村長", sprite: "mayor", home: [15, 9], lines: ["有問題儘管問！", "我來分派工作", "點我開始問答"], skill: "分派工作" };
/** 工匠：在「村民 → 用對話建立」幫忙打造村民，不接問答任務 */
export const CRAFTSMAN: VillagerInfo = { id: "craftsman", name: "工匠", sprite: "craftsman", home: [26, 16], lines: ["想要新村民嗎？", "我會打造村民", "敲敲打打～"], skill: "打造村民" };
/** 審核員：最終答案送出前檢查（可在「村規」關閉），事件的 agent 是 reviewer */
export const REVIEWER: VillagerInfo = { id: "reviewer", name: "審核員", sprite: "auditor", home: [10, 12], lines: ["答案我會把關", "仔細檢查中…"], skill: "檢查答案" };
/** 書記：問答後在背景記下使用者的長期記憶，事件的 agent 是 scribe */
export const SCRIBE: VillagerInfo = { id: "scribe", name: "書記", sprite: "scribe", home: [13, 15], lines: ["我會記住你說的", "記下來了～"], skill: "記事情" };
/** 鑑定師：使用者附圖時先看圖，把內容寫成文字交給村長，事件的 agent 是 appraiser */
export const APPRAISER: VillagerInfo = { id: "appraiser", name: "鑑定師", sprite: "appraiser", home: [19, 12], lines: ["有圖給我看看", "我很會看圖喔"], skill: "看圖" };
