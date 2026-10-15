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
