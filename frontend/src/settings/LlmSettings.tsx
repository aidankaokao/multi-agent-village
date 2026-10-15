import { useCallback, useEffect, useState } from "react";
import { ApiError, getAdminToken, llm, setAdminToken, type Model, type Provider, type ProviderKind, type ReasoningEffort, type RemoteModel, type Tiers } from "../api";

const KIND_LABEL: Record<ProviderKind, string> = {
  ollama: "Ollama（本地）",
  openai_compat: "OpenAI 相容（OpenAI / vLLM / LM Studio / 其他雲端）",
  anthropic: "Anthropic（Claude）",
};

const FALLBACK_URL: Record<ProviderKind, string> = {
  ollama: "http://localhost:11434/v1",
  openai_compat: "https://api.openai.com/v1",
  anthropic: "",
};

export function LlmSettings() {
  const [token, setToken] = useState(getAdminToken());
  // 只有後端設定了 VILLAGE_ADMIN_TOKEN 並回 401 時才要求輸入
  const [needToken, setNeedToken] = useState(false);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [tiers, setTiers] = useState<Tiers>({ large: [], small: [] });
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    try {
      const [p, m, t] = await Promise.all([llm.providers(), llm.models(), llm.tiers()]);
      setProviders(p);
      setModels(m);
      setTiers(t);
      setNeedToken(false);
      setError("");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setNeedToken(true);
      setError(String((e as Error).message));
    }
  }, []);

  useEffect(() => {
    reload();
  }, [token, reload]);

  const run = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (needToken) {
    return (
      <div className="panel-body">
        <h3>管理員驗證</h3>
        <p className="hint">
          後端設定了 <code>VILLAGE_ADMIN_TOKEN</code>，請輸入 .env 裡的同一個值。
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const v = new FormData(e.currentTarget).get("token") as string;
            setAdminToken(v.trim());
            setToken(v.trim());
          }}
        >
          <input name="token" type="password" autoComplete="off" placeholder="admin token" defaultValue={token} />
          <button type="submit">確認</button>
        </form>
        {error && <p className="error">{error}</p>}
      </div>
    );
  }

  return (
    <div className="panel-body">
      {error && (
        <p className="error" onClick={() => setError("")}>
          {error}（點擊關閉）
        </p>
      )}
      <ProviderSection providers={providers} models={models} run={run} />
      <TierSection models={models} tiers={tiers} run={run} />
      <TrySection models={models} />
    </div>
  );
}

// ---------------------------------------------------------------- providers

function ProviderSection({ providers, models, run }: { providers: Provider[]; models: Model[]; run: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [kind, setKind] = useState<ProviderKind>("ollama");
  const [defaultUrl, setDefaultUrl] = useState(FALLBACK_URL);
  useEffect(() => {
    llm
      .defaults()
      .then((d) => setDefaultUrl((f) => ({ ...f, ...Object.fromEntries(Object.entries(d.base_urls).filter(([, v]) => v)) })))
      .catch(() => {});
  }, []);
  const [remote, setRemote] = useState<Record<string, RemoteModel[] | string>>({});
  const [editingKey, setEditingKey] = useState<string | null>(null);

  const listRemote = async (id: string) => {
    setRemote((r) => ({ ...r, [id]: "連線中…" }));
    try {
      const list = await llm.remoteModels(id);
      setRemote((r) => ({ ...r, [id]: list }));
    } catch (e) {
      setRemote((r) => ({ ...r, [id]: `❌ ${(e as Error).message}` }));
    }
  };

  return (
    <section>
      <h3>① Provider</h3>
      {providers.map((p) => {
        const r = remote[p.id];
        const registered = models.filter((m) => m.provider_id === p.id);
        return (
          <div key={p.id} className="card">
            <div className="row">
              <strong>{p.name}</strong>
              <span className="tag">{p.kind}</span>
              <label className="toggle">
                <input type="checkbox" checked={p.enabled} onChange={(e) => run(() => llm.updateProvider(p.id, { enabled: e.target.checked }))} />
                啟用
              </label>
            </div>
            <div className="muted">{p.base_url || "（官方預設端點）"}</div>
            <div className="row">
              <span className="muted">API key：{p.has_api_key ? `🔒 ${p.api_key_hint}` : "未設定"}</span>
              <button className="link" onClick={() => setEditingKey(editingKey === p.id ? null : p.id)}>
                {p.has_api_key ? "更換" : "設定"}
              </button>
              {p.has_api_key && (
                <button className="link danger" onClick={() => confirm("確定清除這個 API key？") && run(() => llm.updateProvider(p.id, { clear_api_key: true }))}>
                  清除
                </button>
              )}
            </div>
            {editingKey === p.id && (
              <form
                className="row"
                onSubmit={(e) => {
                  e.preventDefault();
                  const v = (new FormData(e.currentTarget).get("key") as string).trim();
                  if (v) run(() => llm.updateProvider(p.id, { api_key: v })).then(() => setEditingKey(null));
                }}
              >
                <input name="key" type="password" autoComplete="new-password" placeholder="新的 API key" />
                <button type="submit">儲存（加密）</button>
              </form>
            )}
            <div className="row">
              <button onClick={() => listRemote(p.id)}>測試連線 / 列出模型</button>
              <button className="danger" onClick={() => confirm(`刪除 ${p.name}？其下 ${registered.length} 個模型也會移除`) && run(() => llm.deleteProvider(p.id))}>
                刪除
              </button>
            </div>
            {typeof r === "string" && <div className="muted">{r}</div>}
            {Array.isArray(r) && (
              <ul className="remote-list">
                {r.length === 0 && <li className="muted">沒有可用模型</li>}
                {r.map((m) => (
                  <li key={m.id}>
                    <code>{m.id}</code>
                    {m.registered || registered.some((x) => x.model === m.id) ? (
                      <span className="tag ok">已註冊</span>
                    ) : (
                      <button
                        className="link"
                        onClick={() =>
                          run(() => llm.createModel({ provider_id: p.id, model: m.id, display_name: m.display_name, context_window: m.context_window }))
                        }
                      >
                        ＋註冊
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}

      <form
        className="card"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          const form = e.currentTarget;
          const api_key = (f.get("api_key") as string).trim();
          run(() =>
            llm.createProvider({
              name: (f.get("name") as string).trim(),
              kind,
              base_url: (f.get("base_url") as string).trim() || undefined,
              api_key: api_key || undefined,
            }),
          ).then(() => form.reset());
        }}
      >
        <strong>新增 Provider</strong>
        <select value={kind} onChange={(e) => setKind(e.target.value as ProviderKind)}>
          {(Object.keys(KIND_LABEL) as ProviderKind[]).map((k) => (
            <option key={k} value={k}>
              {KIND_LABEL[k]}
            </option>
          ))}
        </select>
        <input name="name" required placeholder="名稱，例如 本機 Ollama" />
        <input name="base_url" key={kind + defaultUrl[kind]} defaultValue={defaultUrl[kind]} placeholder={kind === "anthropic" ? "留空 = 官方端點" : "base URL"} />
        <input name="api_key" type="password" autoComplete="new-password" placeholder={kind === "ollama" ? "API key（本地通常留空）" : "API key"} />
        <p className="hint">API key 送出後以 AES-256-GCM 加密存入資料庫，之後畫面只會顯示遮罩。</p>
        <button type="submit">新增</button>
      </form>
    </section>
  );
}

// ---------------------------------------------------------------- tiers

function TierSection({ models, tiers, run }: { models: Model[]; tiers: Tiers; run: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [draft, setDraft] = useState<Tiers>(tiers);
  useEffect(() => setDraft(tiers), [tiers]);
  const byId = Object.fromEntries(models.map((m) => [m.id, m]));
  const dirty = JSON.stringify(draft) !== JSON.stringify(tiers);

  const move = (tier: keyof Tiers, i: number, d: number) =>
    setDraft((t) => {
      const list = [...t[tier]];
      const j = i + d;
      if (j < 0 || j >= list.length) return t;
      [list[i], list[j]] = [list[j], list[i]];
      return { ...t, [tier]: list };
    });

  return (
    <section>
      <h3>② 已註冊模型與分級</h3>
      {models.length === 0 && <p className="muted">先在上方 provider「列出模型」並註冊。</p>}
      {models.length > 0 && (
        <table>
          <tbody>
            {models.map((m) => (
              <tr key={m.id}>
                <td>
                  <code>{m.model}</code>
                  <div className="muted">{m.provider_name}</div>
                </td>
                <td>
                  <label className="toggle" title="能否用 JSON schema 約束輸出（村長規劃需要）">
                    <input type="checkbox" checked={m.supports_json_schema} onChange={(e) => run(() => llm.updateModel(m.id, { supports_json_schema: e.target.checked }))} />
                    JSON
                  </label>
                </td>
                <td>
                  <select
                    title="思考強度：思考型模型（如 qwen3.5）設 none 可大幅加速，也避免 token 全花在思考上"
                    value={m.reasoning_effort ?? ""}
                    onChange={(e) => run(() => llm.updateModel(m.id, { reasoning_effort: (e.target.value || null) as ReasoningEffort | null }))}
                  >
                    <option value="">思考：預設</option>
                    <option value="none">思考：none</option>
                    <option value="low">思考：low</option>
                    <option value="medium">思考：medium</option>
                    <option value="high">思考：high</option>
                  </select>
                </td>
                <td>
                  <label className="toggle">
                    <input type="checkbox" checked={m.enabled} onChange={(e) => run(() => llm.updateModel(m.id, { enabled: e.target.checked }))} />
                    啟用
                  </label>
                </td>
                <td>
                  <button className="link danger" onClick={() => run(() => llm.deleteModel(m.id))}>
                    移除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="tiers">
        {(["large", "small"] as const).map((tier) => (
          <div key={tier} className="card">
            <strong>{tier === "large" ? "large（村長、困難任務）" : "small（簡單任務、備援）"}</strong>
            <ol>
              {draft[tier].map((id, i) => (
                <li key={id}>
                  <code>{byId[id]?.model ?? id}</code>
                  <button className="link" onClick={() => move(tier, i, -1)}>↑</button>
                  <button className="link" onClick={() => move(tier, i, 1)}>↓</button>
                  <button className="link danger" onClick={() => setDraft((t) => ({ ...t, [tier]: t[tier].filter((x) => x !== id) }))}>✕</button>
                </li>
              ))}
            </ol>
            <select
              value=""
              onChange={(e) => e.target.value && setDraft((t) => ({ ...t, [tier]: [...t[tier], e.target.value] }))}
            >
              <option value="">＋加入模型…</option>
              {models
                .filter((m) => !draft[tier].includes(m.id))
                .map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.model}（{m.provider_name}）
                  </option>
                ))}
            </select>
          </div>
        ))}
      </div>
      <p className="hint">排在前面的優先使用，失敗依序往下；large 全部失敗時會再退到 small。</p>
      <button disabled={!dirty} onClick={() => run(() => llm.saveTiers(draft))}>
        儲存分級
      </button>
    </section>
  );
}

// ---------------------------------------------------------------- try

function TrySection({ models }: { models: Model[] }) {
  const [target, setTarget] = useState("tier:large");
  const [prompt, setPrompt] = useState("用一句話介紹你自己");
  const [out, setOut] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <section>
      <h3>③ 試打</h3>
      <div className="row">
        <select value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="tier:large">tier: large</option>
          <option value="tier:small">tier: small</option>
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.model}
            </option>
          ))}
        </select>
      </div>
      <textarea rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      <button
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setOut("");
          try {
            const r = await llm.tryModel(target, prompt);
            setOut(`[${r.model}]\n${r.text}`);
          } catch (e) {
            setOut(`❌ ${(e as Error).message}`);
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "等待回應…" : "送出"}
      </button>
      {out && <pre className="output">{out}</pre>}
    </section>
  );
}
