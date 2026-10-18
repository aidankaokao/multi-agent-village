/** 登入用 httpOnly cookie（瀏覽器自動帶上，前端拿不到 token）。任何 API 回 401 就通知 App 回到登入頁。 */
export const UNAUTHORIZED = "village:unauthorized";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = init;
  const res = await fetch(path, {
    ...rest,
    headers: {
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...rest.headers,
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* 非 JSON 回應 */
    }
    if (res.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event(UNAUTHORIZED));
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

// ---------------------------------------------------------------- LLM 設定

export type ProviderKind = "ollama" | "openai_compat" | "anthropic";

export interface Provider {
  id: string;
  name: string;
  kind: ProviderKind;
  base_url: string | null;
  enabled: boolean;
  has_api_key: boolean;
  api_key_hint: string | null;
}

export interface RemoteModel {
  id: string;
  display_name: string | null;
  context_window: number | null;
  registered: boolean;
}

export interface Model {
  id: string;
  provider_id: string;
  provider_name: string;
  model: string;
  display_name: string | null;
  supports_json_schema: boolean;
  context_window: number | null;
  reasoning_effort: ReasoningEffort | null;
  vision_verified_at: string | null; // 看圖測驗通過的時間
  output: ModelOutput; // image = 生圖模型、audio = 語音轉文字模型（只能放各自的分級）
  image_verified_at: string | null; // 生圖測驗通過的時間
  audio_verified_at: string | null; // 轉寫測驗通過的時間
  enabled: boolean;
}

export type ReasoningEffort = "none" | "low" | "medium" | "high";
export type ModelOutput = "text" | "image" | "audio";

export interface Tiers {
  large: string[];
  small: string[];
  vision: string[]; // 鑑定師看圖專用，不和 large / small 互相備援
  image: string[]; // 畫家生圖專用（生圖模型）
  audio: string[]; // 聽打員專用（語音轉文字模型）
}

export interface AudioTestResult {
  passed: boolean;
  reason: string;
  answer: string;
  expected: string;
  score: number; // 0～1：和錄音內容的吻合程度
  elapsed_ms: number;
  model: Model;
}

export interface ImageTestResult {
  passed: boolean;
  reason: string;
  image_url: string | null;
  elapsed_ms: number;
  cost: number | null; // 美元
  check: string | null; // 鑑定師驗收（有視覺模型時）
  model: Model;
}

export interface AppraisalInfo {
  ok: boolean;
  text: string;
  error: string;
  count: number;
  relook: boolean; // 追問時重看前面附的圖
  model: string | null;
  elapsed_ms: number;
  tokens_in: number;
  tokens_out: number;
}

export interface VisionTestResult {
  passed: boolean;
  reason: string;
  answer: string;
  expected_number: string;
  expected_color: string;
  image: string;
  model: Model;
}

export const llm = {
  defaults: () => api<{ base_urls: Partial<Record<ProviderKind, string | null>> }>("/api/llm/defaults"),
  providers: () => api<Provider[]>("/api/llm/providers"),
  createProvider: (body: { name: string; kind: ProviderKind; base_url?: string; api_key?: string }) =>
    api<Provider>("/api/llm/providers", { method: "POST", json: body }),
  updateProvider: (id: string, body: Partial<{ name: string; base_url: string; api_key: string; clear_api_key: boolean; enabled: boolean }>) =>
    api<Provider>(`/api/llm/providers/${id}`, { method: "PATCH", json: body }),
  deleteProvider: (id: string) => api<void>(`/api/llm/providers/${id}`, { method: "DELETE" }),
  remoteModels: (id: string, output: ModelOutput = "text") =>
    api<RemoteModel[]>(`/api/llm/providers/${id}/remote-models?output=${output}`),
  models: () => api<Model[]>("/api/llm/models"),
  createModel: (body: {
    provider_id: string;
    model: string;
    display_name?: string | null;
    context_window?: number | null;
    output?: ModelOutput;
  }) =>
    api<Model>("/api/llm/models", { method: "POST", json: body }),
  updateModel: (id: string, body: Partial<Pick<Model, "display_name" | "supports_json_schema" | "reasoning_effort" | "enabled">>) =>
    api<Model>(`/api/llm/models/${id}`, { method: "PATCH", json: body }),
  deleteModel: (id: string) => api<void>(`/api/llm/models/${id}`, { method: "DELETE" }),
  tiers: () => api<Tiers>("/api/llm/tiers"),
  saveTiers: (t: Tiers) => api<Tiers>("/api/llm/tiers", { method: "PUT", json: t }),
  imageTest: (id: string) => api<ImageTestResult>(`/api/llm/models/${id}/image-test`, { method: "POST" }),
  audioTest: (id: string) => api<AudioTestResult>(`/api/llm/models/${id}/audio-test`, { method: "POST" }),
  visionTest: (id: string) => api<VisionTestResult>(`/api/llm/models/${id}/vision-test`, { method: "POST" }),
  tryModel: (target: string, prompt: string) =>
    api<{ text: string; model: string }>("/api/llm/try", { method: "POST", json: { target, prompt } }),
};

// ---------------------------------------------------------------- 村民 / 問答

export interface AgentInfo {
  id: string;
  name: string;
  description: string;
  kind: string;
  sprite: string | null;
  home: [number, number] | null;
  skills: { id: string; description: string; examples: string[] }[];
  tags: string[];
  status?: ResidentStatus | null; // 駐村專家才有
  accepts?: string[]; // 駐村專家收哪些文件（副檔名）
  tool?: string | null; // 工具型村民用的工具（有 typist = 可以附錄音）
}

export interface AudioReady {
  ok: boolean;
  reason: string | null; // 不能轉寫的原因
  max_bytes: number;
  extensions: string[];
  max_minutes: number;
}

export interface UploadedAudio {
  id: string;
  name: string;
  size: number;
}

/** 上傳錄音（檔案原始內容當 body）；用 XHR 才拿得到上傳進度。signal 中止時 reject。 */
export function uploadAudio(file: File, onProgress: (ratio: number) => void, signal?: AbortSignal): Promise<UploadedAudio> {
  return new Promise((ok, fail) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/files/audio?name=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let body: { detail?: unknown } & Partial<UploadedAudio> = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* 非 JSON（例如 nginx 的 413 頁面） */
      }
      if (xhr.status >= 200 && xhr.status < 300) return ok(body as UploadedAudio);
      if (xhr.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED));
      const detail = typeof body.detail === "string" ? body.detail : xhr.status === 413 ? "檔案太大" : `上傳失敗（HTTP ${xhr.status}）`;
      fail(new ApiError(xhr.status, detail));
    };
    xhr.onerror = () => fail(new Error("上傳失敗：連線中斷"));
    xhr.onabort = () => fail(new DOMException("aborted", "AbortError"));
    signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(file);
  });
}

export const village = {
  agents: () => api<{ agents: AgentInfo[]; errors: Record<string, string> }>("/api/agents"),
  /** images：data URL（前端已縮圖成 JPEG） */
  /** files：文件（base64），只交給收文件的駐村專家 */
  /** audioIds：先用 uploadAudio 上傳好的錄音 */
  chat: (
    message: string,
    conversation_id: string | null,
    images: string[] = [],
    files: { name: string; data: string }[] = [],
    audioIds: string[] = [],
  ) =>
    api<{ run_id: string; conversation_id: string; image_ids: string[]; files: { id: string; name: string; size: number; audio?: boolean }[] }>(
      "/api/chat",
      {
        method: "POST",
        json: {
          message,
          conversation_id,
          images: images.map((data) => ({ data })),
          files: files.map(({ name, data }) => ({ name, data })),
          audio_ids: audioIds,
        },
      },
    ),
  audioReady: () => api<AudioReady>("/api/files/audio/ready"),
  cancel: (runId: string) => api<{ ok: boolean }>(`/api/chat/${runId}/cancel`, { method: "POST" }),
};

// ---------------------------------------------------------------- 執行紀錄與村規

export type RunStatus = "running" | "done" | "error" | "cancelled" | "interrupted";

export interface RunSummary {
  id: string;
  user: string | null; // 提問者（管理員看全部紀錄時用）
  message: string;
  status: RunStatus;
  agents: string[];
  review: boolean | null;
  elapsed_ms: number | null;
  tokens_in: number;
  tokens_out: number;
  created_at: string;
}

export interface TraceStep {
  id: string;
  agent: string;
  instruction: string;
  use: string[];
  status: string;
  output: string;
  error: string | null;
  model: string | null;
  elapsed_ms: number;
  attempts: number;
  warnings: string[];
  input: string;
  retry_reasons: string[];
  tokens_in: number;
  tokens_out: number;
  rejected: { output: string; reason: string }[];
  attachments: Attachment[];
  tool_args: Record<string, unknown> | null;
  round?: number; // 迴圈的第幾輪
  verdict?: LoopVerdict | null; // 迴圈的審查步驟
  history?: { round: number; output: string; verdict: LoopVerdict | null; elapsed_ms: number }[]; // 迴圈前幾輪
}

/** 迴圈：審查結論 */
export interface LoopVerdict {
  passed: boolean;
  feedback: string;
  how: string; // 結論 / LLM 判斷 / 判斷不出來，當作通過
}

export interface LoopInfo {
  check: string;
  back_to: string;
  max_rounds: number; // 0 = 用村規的上限
}

export interface TraceReview {
  passed: boolean;
  reason: string;
  fix: string;
  step: string | null;
  model: string | null;
  elapsed_ms: number;
  tokens_in: number;
  tokens_out: number;
}

export interface RunDetail extends RunSummary {
  answer: string | null;
  error: string | null;
  plan: {
    steps: { id: string; agent: string; instruction: string; use: string[] }[];
    source: string;
    notes: string[];
    appraisal?: AppraisalInfo; // 鑑定師看圖的結果
    loop?: LoopInfo | null; // 改到過關為止
  } | null;
  steps: TraceStep[];
  reviews: TraceReview[];
  finished_at: string | null;
}

export interface VillageRules {
  allow_registration: boolean;
  context_turns: number;
  memory_enabled: boolean;
  memory_auto: boolean;
  memory_max: number;
  max_images: number;
  paint_per_run: number;
  paint_per_day: number;
  loop_max_rounds: number; // 迴圈（審查不通過就重做）最多幾輪
  transcribe_max_minutes: number; // 聽打員每份錄音最長幾分鐘（0 = 不開放）
  reviewer_enabled: boolean;
  reviewer_target: string;
  review_redo: number;
  step_timeout_s: number;
  parallel: boolean;
  max_parallel: number;
  keep_runs: number;
}

export const traces = {
  list: (p: { before?: string; q?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams(Object.entries(p).filter(([, v]) => v !== undefined && v !== "") as [string, string][]);
    return api<RunSummary[]>(`/api/runs?${qs}`);
  },
  get: (id: string) => api<RunDetail>(`/api/runs/${id}`),
  remove: (id: string) => api<void>(`/api/runs/${id}`, { method: "DELETE" }),
  clear: () => api<void>("/api/runs", { method: "DELETE" }),
};

export const rules = {
  get: () => api<VillageRules>("/api/rules"),
  save: (r: VillageRules) => api<VillageRules>("/api/rules", { method: "PUT", json: r }),
};

// ---------------------------------------------------------------- 村民管理

/** 與 agent.yaml 相同的結構 */
export interface Expect {
  contains: string[];
  not_contains: string[];
  lang: "zh" | "en" | null;
  min_chars: number | null;
  max_chars: number | null;
  regex: string | null;
}

export interface AgentTest {
  input: string;
  context: string;
  expect: Expect;
}

export interface AgentSpec {
  card: {
    id: string;
    name: string;
    description: string;
    version: string;
    skills: { id: string; description: string; examples: string[] }[];
    tags: string[];
  };
  "x-village": { sprite: string | null; home: [number, number] | null; keywords: string[] };
  kind: "prompt" | "tool" | "remote"; // remote = 駐村專家（外接的獨立 agent）
  tool?: { id: string; explain: boolean } | null; // 工具型村民：用哪個工具
  remote?: { url: string; a2a_path: string; share_llm: boolean; accepts?: string[] } | null; // 駐村專家的連線（token 另外加密保存）；accepts：收哪些文件
  model: { tier: "large" | "small"; model_id: string | null; temperature: number | null; max_tokens: number };
  prompt: { system: string; user_template: string };
  validate: { must_not_be_empty: boolean; max_chars: number | null };
  tests: AgentTest[];
}

export interface Villager {
  id: string;
  enabled: boolean;
  builtin: boolean;
  spec: AgentSpec;
  updated_at: string;
}

export interface TryResult {
  ok: boolean;
  output: string;
  model: string | null;
  elapsed_ms: number;
  attempts: number;
  warnings: string[];
  retry_reasons: string[];
  error: string | null;
  failures: string[];
  attachments: Attachment[];
  tool_args: Record<string, unknown> | null;
}

/** 工具產生的附件（目前是 SVG 圖表） */
export interface Attachment {
  kind: "svg" | "image" | "file"; // svg：data 是 SVG 原始碼；image：畫家的畫作；file：駐村專家回傳的檔案（報告 PDF）。後兩者用 url 讀取（保存 14 天）
  name: string;
  data: string;
  title: string;
  url?: string;
}

/** 工具箱裡的工具 */
export interface ToolInfo {
  id: string;
  name: string;
  description: string;
  default_prompt: string;
  keywords: string[];
  examples: string[];
}

export const villagers = {
  tools: () => api<ToolInfo[]>("/api/villagers/tools"),
  list: () => api<Villager[]>("/api/villagers"),
  create: (spec: AgentSpec) => api<Villager>("/api/villagers", { method: "POST", json: { spec } }),
  update: (spec: AgentSpec) => api<Villager>(`/api/villagers/${spec.card.id}`, { method: "PUT", json: { spec } }),
  setEnabled: (id: string, enabled: boolean) => api<Villager>(`/api/villagers/${id}`, { method: "PATCH", json: { enabled } }),
  remove: (id: string) => api<void>(`/api/villagers/${id}`, { method: "DELETE" }),
  restoreBuiltin: () => api<{ restored: string[] }>("/api/villagers/restore-builtin", { method: "POST" }),
  toYaml: (spec: AgentSpec) => api<{ yaml: string }>("/api/villagers/to-yaml", { method: "POST", json: { spec } }),
  fromYaml: (yaml: string) => api<{ spec: AgentSpec }>("/api/villagers/from-yaml", { method: "POST", json: { yaml } }),
  try: (body: { spec: AgentSpec; instruction: string; context?: string; target?: string | null; expect?: Expect }) =>
    api<TryResult>("/api/villagers/try", { method: "POST", json: body }),
};

// ---------------------------------------------------------------- 駐村專家（外接的獨立 agent）

export type ResidentStatus = "online" | "offline" | "unknown";

export interface A2ACard {
  name: string;
  description: string;
  url?: string;
  version?: string;
  skills?: { id?: string; name?: string; description?: string; examples?: string[]; tags?: string[] }[];
  "x-llm-headers"?: Record<string, string>;
}

export interface ResidentPreview {
  url: string;
  card: A2ACard;
  a2a_path: string;
  suggested_id: string;
  online: boolean;
  health_error: string | null;
  wants_llm: boolean; // 對方需要呼叫端提供 LLM（例如台灣史專家）
  accepts: string[]; // 收哪些文件（副檔名），例如文件彙整師
}

export interface Resident {
  id: string;
  url: string;
  a2a_path: string;
  share_llm: boolean;
  has_token: boolean;
  token_hint: string | null;
  status: ResidentStatus;
  checked_at: string | null;
  last_error: string | null;
  card: A2ACard | null;
  card_fetched_at: string | null;
}

export const residents = {
  list: () => api<Resident[]>("/api/residents"),
  preview: (url: string) => api<ResidentPreview>("/api/residents/preview", { method: "POST", json: { url } }),
  invite: (body: { id: string; url: string; token?: string; name?: string; sprite?: string | null; share_llm: boolean }) =>
    api<Resident>("/api/residents", { method: "POST", json: body }),
  update: (id: string, body: Partial<{ url: string; token: string; clear_token: boolean; share_llm: boolean }>) =>
    api<Resident>(`/api/residents/${id}`, { method: "PATCH", json: body }),
  refresh: (id: string) => api<Resident>(`/api/residents/${id}/refresh`, { method: "POST" }),
  check: (id: string) => api<Resident>(`/api/residents/${id}/check`, { method: "POST" }),
};

// ---------------------------------------------------------------- 村民工坊（工匠）

export interface WorkshopMsg {
  role: "user" | "assistant";
  content: string;
}

export interface WorkshopBody {
  messages: WorkshopMsg[];
  sprites: { id: string; name: string; desc: string }[];
  draft: AgentSpec | null;
  target: string | null;
  blocked: [number, number][]; // 地圖上有樹、房子的格子，工匠挑住處時避開
}

export const workshop = {
  interview: (body: WorkshopBody) =>
    api<{ reply: string; ready: boolean; source: string }>("/api/workshop/interview", { method: "POST", json: body }),
  generate: (body: WorkshopBody) =>
    api<{ spec: AgentSpec; notes: string[]; summary: string; source: "llm" | "fallback"; model: string | null }>("/api/workshop/generate", {
      method: "POST",
      json: body,
    }),
};

// ---------------------------------------------------------------- 帳號

export type Role = "admin" | "member";

export interface Me {
  id: string;
  username: string;
  display_name: string;
  role: Role;
}

export interface AuthStatus {
  setup_required: boolean; // 還沒有任何帳號：第一位註冊的人成為管理員
  allow_registration: boolean;
  user: Me | null;
  llm_ready: boolean; // 自己的 LLM 設定好了沒（每個帳號用自己的 LLM 與額度）
  vision_ready: boolean; // 有通過看圖測驗的視覺模型（vision 分級）→ 可以附圖
  max_images: number; // 村規：每次最多附幾張圖（0 = 不開放）
}

export interface UserRow extends Me {
  enabled: boolean;
  created_at: string;
  last_login_at: string | null;
}

export const auth = {
  status: () => api<AuthStatus>("/api/auth/status"),
  login: (username: string, password: string) => api<{ user: Me }>("/api/auth/login", { method: "POST", json: { username, password } }),
  register: (username: string, password: string, display_name: string) =>
    api<{ user: Me; first: boolean }>("/api/auth/register", { method: "POST", json: { username, password, display_name } }),
  logout: () => api<void>("/api/auth/logout", { method: "POST" }),
  changePassword: (old_password: string, new_password: string) =>
    api<void>("/api/auth/password", { method: "POST", json: { old_password, new_password } }),
  deleteAccount: (password: string) => api<void>("/api/auth/delete-account", { method: "POST", json: { password } }),
};

export const users = {
  list: () => api<UserRow[]>("/api/users"),
  create: (body: { username: string; password: string; display_name: string; role: Role }) =>
    api<UserRow>("/api/users", { method: "POST", json: body }),
  update: (id: string, body: Partial<{ display_name: string; role: Role; enabled: boolean; password: string }>) =>
    api<UserRow>(`/api/users/${id}`, { method: "PATCH", json: body }),
  remove: (id: string) => api<void>(`/api/users/${id}`, { method: "DELETE" }),
};

// ---------------------------------------------------------------- 對話紀錄

export interface ConversationRow {
  id: string;
  title: string;
  messages: number;
  created_at: string;
  updated_at: string;
}

export interface MessageRow {
  id: string;
  role: "user" | "assistant";
  content: string;
  run_id: string | null;
  status: "done" | "error" | "cancelled";
  meta: {
    attachments?: Attachment[];
    agents?: string[];
    review?: boolean | null;
    images?: string[];
    files?: { id: string; name: string; size: number; audio?: boolean }[]; // audio：錄音（轉完就刪掉，不能下載）
  } | null;
  created_at: string;
}

export const conversations = {
  list: () => api<ConversationRow[]>("/api/conversations"),
  messages: (id: string) => api<MessageRow[]>(`/api/conversations/${id}/messages`),
  rename: (id: string, title: string) => api<{ id: string; title: string }>(`/api/conversations/${id}`, { method: "PATCH", json: { title } }),
  remove: (id: string) => api<void>(`/api/conversations/${id}`, { method: "DELETE" }),
};

// ---------------------------------------------------------------- 長期記憶

export interface MemoryRow {
  id: string;
  content: string;
  kind: "preference" | "fact";
  source: "auto" | "manual";
  enabled: boolean;
  use_count: number;
  created_at: string;
  last_used_at: string | null;
}

export const memories = {
  list: () => api<MemoryRow[]>("/api/memories"),
  create: (content: string, kind: MemoryRow["kind"]) => api<MemoryRow>("/api/memories", { method: "POST", json: { content, kind } }),
  update: (id: string, body: Partial<Pick<MemoryRow, "content" | "kind" | "enabled">>) =>
    api<MemoryRow>(`/api/memories/${id}`, { method: "PATCH", json: body }),
  remove: (id: string) => api<void>(`/api/memories/${id}`, { method: "DELETE" }),
  clear: () => api<void>("/api/memories", { method: "DELETE" }),
};
