/**
 * 村民的生活作息（只是看起來在過日子；派工時立刻中斷）。
 *
 * 這裡只有「做什麼」：活動表、依角色挑活動、依時段的作息。不依賴畫法，
 * 3D 版決定「在哪裡、怎麼擺姿勢」：地點在場景（world3d/town.ts、各場景的 Terrain），姿勢與道具在 world3d/Villager3D.ts。
 */

/** 活動的地點：地圖上已經有的東西（house = 自己家門口，駐村專家是小島上的小屋） */
export type SpotKind = "workbench" | "well" | "pond" | "tree" | "sign" | "flowers" | "plaza" | "house" | "bench" | "timetable" | "field";

export type Pose = "stand" | "sit" | "fish" | "hammer" | "read" | "write" | "water" | "stretch" | "sleep" | "eat" | "look" | "sweep" | "paint" | "type" | "dance";

export type Prop = "rod" | "book" | "notebook" | "can" | "riceball" | "broom" | "telescope" | "paper" | "easel" | "cup" | "abacus" | "scroll";

export interface Activity {
  id: string;
  icon: string; // 頭上的小圖示
  spot: SpotKind;
  pose: Pose;
  prop?: Prop;
  dur: [number, number]; // 秒
  social?: boolean; // 做這件事的時候可以和別人聊天（廣場、井邊）
  lines?: string[]; // 做這件事時偶爾冒的一句
}

const A = (a: Activity): Activity => a;

export const ACTIVITIES: Record<string, Activity> = {
  // ---- 依角色
  patrol: A({ id: "patrol", icon: "👀", spot: "plaza", pose: "look", dur: [14, 26], social: true, lines: ["今天村裡很平靜", "大家都好嗎？"] }),
  notice: A({ id: "notice", icon: "📋", spot: "sign", pose: "read", prop: "paper", dur: [10, 18], lines: ["貼張公告…", "有新消息嗎？"] }),
  compose: A({ id: "compose", icon: "✍️", spot: "tree", pose: "write", prop: "notebook", dur: [16, 30], lines: ["靈感來了！", "這句再改改…"] }),
  muse: A({ id: "muse", icon: "🌸", spot: "pond", pose: "sit", dur: [12, 22], lines: ["水面好安靜…"] }),
  paint: A({ id: "paint", icon: "🎨", spot: "flowers", pose: "paint", prop: "easel", dur: [18, 32], lines: ["這個顏色不錯", "再一筆就好"] }),
  type: A({ id: "type", icon: "🎧", spot: "tree", pose: "type", dur: [16, 30], lines: ["噠噠噠噠…"] }),
  study: A({ id: "study", icon: "📖", spot: "tree", pose: "read", prop: "book", dur: [16, 30], lines: ["這本書好看", "原來如此…"] }),
  abacus: A({ id: "abacus", icon: "🧮", spot: "workbench", pose: "write", prop: "abacus", dur: [14, 26], lines: ["一加一…等於二", "帳要對清楚"] }),
  chart: A({ id: "chart", icon: "📈", spot: "sign", pose: "write", prop: "paper", dur: [14, 24], lines: ["這條線往上走"] }),
  hammer: A({ id: "hammer", icon: "🔨", spot: "workbench", pose: "hammer", dur: [16, 30], lines: ["敲敲打打～", "做個新東西"] }),
  proofread: A({ id: "proofread", icon: "🖍️", spot: "sign", pose: "read", prop: "paper", dur: [12, 22], lines: ["這裡有錯字…"] }),
  inspect: A({ id: "inspect", icon: "🔍", spot: "flowers", pose: "look", dur: [10, 18], lines: ["這朵花品相不錯"] }),
  juggle: A({ id: "juggle", icon: "🤹", spot: "plaza", pose: "dance", dur: [10, 18], social: true, lines: ["看我的！", "哈哈哈～"] }),
  farm: A({ id: "farm", icon: "🌱", spot: "flowers", pose: "water", prop: "can", dur: [12, 20], lines: ["快快長大"] }),
  cook: A({ id: "cook", icon: "🍲", spot: "well", pose: "hammer", dur: [14, 24], social: true, lines: ["今天煮什麼好？"] }),
  oldbooks: A({ id: "oldbooks", icon: "📜", spot: "tree", pose: "read", prop: "scroll", dur: [18, 32], lines: ["這段史料很有意思"] }),
  newsprint: A({ id: "newsprint", icon: "📰", spot: "tree", pose: "read", prop: "paper", dur: [16, 28], lines: ["明天的頭條是…"] }),
  scout: A({ id: "scout", icon: "🔭", spot: "tree", pose: "look", prop: "telescope", dur: [14, 26], lines: ["遠方有新消息"] }),
  sort: A({ id: "sort", icon: "🗂️", spot: "workbench", pose: "write", prop: "paper", dur: [14, 26], lines: ["文件整理好了"] }),
  // ---- 大家都會做的
  fish: A({ id: "fish", icon: "🎣", spot: "pond", pose: "fish", prop: "rod", dur: [18, 34], lines: ["魚兒上鉤吧…", "今天魚不咬餌…"] }),
  water: A({ id: "water", icon: "🌱", spot: "flowers", pose: "water", prop: "can", dur: [10, 18], lines: ["花開得真好"] }),
  rest: A({ id: "rest", icon: "🍃", spot: "tree", pose: "sit", dur: [12, 22], lines: ["樹蔭下好涼"] }),
  sweep: A({ id: "sweep", icon: "🧹", spot: "house", pose: "sweep", prop: "broom", dur: [10, 18] }),
  bucket: A({ id: "bucket", icon: "🪣", spot: "well", pose: "stand", dur: [8, 14], social: true, lines: ["打點水"] }),
  // 車站與菜園（鐵道小鎮）
  waitTrain: A({ id: "waitTrain", icon: "🎫", spot: "bench", pose: "sit", dur: [14, 26], social: true, lines: ["火車快來了吧", "等車中…"] }),
  timetable: A({ id: "timetable", icon: "🕒", spot: "timetable", pose: "read", dur: [8, 14], lines: ["下一班幾點？"] }),
  garden: A({ id: "garden", icon: "🥬", spot: "field", pose: "water", prop: "can", dur: [14, 24], lines: ["菜長得真好", "今年會豐收"] }),
  // ---- 依時段
  stretch: A({ id: "stretch", icon: "🙆", spot: "plaza", pose: "stretch", dur: [10, 18], social: true, lines: ["早安！", "伸個懶腰～"] }),
  eat: A({ id: "eat", icon: "🍙", spot: "tree", pose: "eat", prop: "riceball", dur: [14, 24], lines: ["好吃！", "午餐時間～"] }),
  tea: A({ id: "tea", icon: "☕", spot: "plaza", pose: "stand", prop: "cup", dur: [16, 28], social: true, lines: ["今天辛苦了", "晚霞好美"] }),
  sleep: A({ id: "sleep", icon: "💤", spot: "house", pose: "sleep", dur: [60, 120] }),
};

/** 依外觀猜常做的事 */
const BY_SPRITE: Record<string, string[]> = {
  mayor: ["patrol", "notice"],
  poet: ["compose", "muse"],
  painter: ["paint"],
  typist: ["type"],
  scholar: ["study"],
  merchant: ["abacus"],
  craftsman: ["hammer"],
  reviewer: ["proofread"],
  auditor: ["proofread", "notice"],
  scribe: ["compose", "study"],
  appraiser: ["inspect"],
  jester: ["juggle"],
  farmer: ["farm", "garden"],
  chef: ["cook"],
  translator: ["study"],
};

/** 名字、技能裡的關鍵字比外觀準（例如同樣是學者外觀的摘要師和圖表村民、駐村專家） */
const BY_WORD: [RegExp, string[]][] = [
  [/報社|報紙|日報/, ["newsprint"]],
  [/歷史|史/, ["oldbooks"]],
  [/研究|搜尋|網路|新聞/, ["scout"]],
  [/文件|彙整|報告/, ["sort"]],
  [/圖表/, ["chart"]],
  [/計算|算/, ["abacus"]],
  [/畫/, ["paint"]],
  [/詩/, ["compose"]],
  [/聽打|逐字稿/, ["type"]],
  [/摘要|整理/, ["study"]],
  [/翻譯/, ["study"]],
  [/笑話/, ["juggle"]],
];

export interface LifeWho {
  id: string;
  name: string;
  sprite?: string | null;
  skill?: string;
}

/** 這位村民的拿手活動 */
export function rolesOf(v: LifeWho): string[] {
  const text = `${v.name} ${v.skill ?? ""}`;
  for (const [re, ids] of BY_WORD) if (re.test(text)) return ids;
  return BY_SPRITE[v.sprite ?? ""] ?? ["study"];
}

const COMMON = ["fish", "water", "rest", "sweep", "bucket", "waitTrain", "timetable", "garden"];

export type Period = "night" | "dawn" | "day" | "noon" | "evening";

/** 一天的時段（分鐘 0～1439） */
export function periodOf(min: number): Period {
  if (min >= 1320 || min < 330) return "night"; // 22:00～5:30
  if (min < 450) return "dawn"; // ～7:30
  if (min >= 690 && min < 780) return "noon"; // 11:30～13:00
  if (min >= 1050 && min < 1170) return "evening"; // 17:30～19:30
  return "day";
}

/**
 * 下一件事：null = 隨便走走。
 * 平常大約六成做拿手的事、兩成五散步、一成五做大家都會做的事；清晨、中午、傍晚有一半照時段，晚上回家睡覺。
 */
export function nextActivity(roles: string[], period: Period, last: string | null, rand = Math.random): Activity | null {
  if (period === "night") return ACTIVITIES.sleep;
  const timed = { dawn: "stretch", noon: "eat", evening: "tea" } as Record<Period, string | undefined>;
  const t = timed[period];
  if (t && t !== last && rand() < 0.5) return ACTIVITIES[t];
  const r = rand();
  if (r < 0.25) return null;
  const pool = r < 0.85 ? roles : COMMON;
  const choices = pool.filter((id) => id !== last);
  const id = (choices.length ? choices : pool)[Math.floor(rand() * (choices.length || pool.length))];
  return ACTIVITIES[id] ?? null;
}
