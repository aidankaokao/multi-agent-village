// admin token 存在瀏覽器 localStorage（僅限本機開發）；Phase 5 帳號系統上線後改為登入 session
const TOKEN_KEY = "village.adminToken";

export function getAdminToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setAdminToken(token: string) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* 無痕模式等情況忽略 */
  }
}

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
      Authorization: `Bearer ${getAdminToken()}`,
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
  enabled: boolean;
}

export type ReasoningEffort = "none" | "low" | "medium" | "high";

export interface Tiers {
  large: string[];
  small: string[];
}

export const llm = {
  defaults: () => api<{ base_urls: Partial<Record<ProviderKind, string | null>> }>("/api/llm/defaults"),
  providers: () => api<Provider[]>("/api/llm/providers"),
  createProvider: (body: { name: string; kind: ProviderKind; base_url?: string; api_key?: string }) =>
    api<Provider>("/api/llm/providers", { method: "POST", json: body }),
  updateProvider: (id: string, body: Partial<{ name: string; base_url: string; api_key: string; clear_api_key: boolean; enabled: boolean }>) =>
    api<Provider>(`/api/llm/providers/${id}`, { method: "PATCH", json: body }),
  deleteProvider: (id: string) => api<void>(`/api/llm/providers/${id}`, { method: "DELETE" }),
  remoteModels: (id: string) => api<RemoteModel[]>(`/api/llm/providers/${id}/remote-models`),
  models: () => api<Model[]>("/api/llm/models"),
  createModel: (body: { provider_id: string; model: string; display_name?: string | null; context_window?: number | null }) =>
    api<Model>("/api/llm/models", { method: "POST", json: body }),
  updateModel: (id: string, body: Partial<Pick<Model, "display_name" | "supports_json_schema" | "reasoning_effort" | "enabled">>) =>
    api<Model>(`/api/llm/models/${id}`, { method: "PATCH", json: body }),
  deleteModel: (id: string) => api<void>(`/api/llm/models/${id}`, { method: "DELETE" }),
  tiers: () => api<Tiers>("/api/llm/tiers"),
  saveTiers: (t: Tiers) => api<Tiers>("/api/llm/tiers", { method: "PUT", json: t }),
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
}

export const village = {
  agents: () => api<{ agents: AgentInfo[]; errors: Record<string, string> }>("/api/agents"),
  chat: (message: string) => api<{ run_id: string }>("/api/chat", { method: "POST", json: { message } }),
  cancel: (runId: string) => api<{ ok: boolean }>(`/api/chat/${runId}/cancel`, { method: "POST" }),
};

// ---------------------------------------------------------------- 執行紀錄與村規

export type RunStatus = "running" | "done" | "error" | "cancelled" | "interrupted";

export interface RunSummary {
  id: string;
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
  plan: { steps: { id: string; agent: string; instruction: string; use: string[] }[]; source: string; notes: string[] } | null;
  steps: TraceStep[];
  reviews: TraceReview[];
  finished_at: string | null;
}

export interface VillageRules {
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
  kind: "prompt";
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
}

export const villagers = {
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
