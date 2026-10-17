import { useCallback, useEffect, useState } from "react";
import { Modal } from "../ui/Modal";
import { llm, type Model, type Provider, type ProviderKind, type ReasoningEffort, type RemoteModel, type Tiers, type VisionTestResult } from "../api";

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
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [tiers, setTiers] = useState<Tiers>({ large: [], small: [], vision: [] });
  const [error, setError] = useState("");
  const [sub, setSub] = useState<Sub | null>(null);

  const reload = useCallback(async () => {
    try {
      const [p, m, t] = await Promise.all([llm.providers(), llm.models(), llm.tiers()]);
      setProviders(p);
      setModels(m);
      setTiers(t);
      setSub((s) => s ?? (p.length === 0 ? "providers" : "tiers")); // 第一次使用直接到供應商
      setError("");
    } catch (e) {
      setError(String((e as Error).message));
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const run: Run = async (fn) => {
    try {
      await fn();
      await reload();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  };

  return (
    <div className="panel-body">
      {error && (
        <p className="error toast" onClick={() => setError("")}>
          {error}（點擊關閉）
        </p>
      )}
      <nav className="subtabs">
        {(
          [
            ["tiers", "分級"],
            ["models", `模型（${models.length}）`],
            ["providers", `供應商（${providers.length}）`],
            ["try", "試打"],
          ] as const
        ).map(([k, label]) => (
          <button key={k} className={sub === k ? "active" : ""} onClick={() => setSub(k)}>
            {label}
          </button>
        ))}
      </nav>
      {sub === "tiers" && <TierSection models={models} tiers={tiers} run={run} goto={setSub} />}
      {sub === "models" && <ModelSection models={models} tiers={tiers} run={run} goto={setSub} />}
      {sub === "providers" && <ProviderSection providers={providers} models={models} run={run} />}
      {sub === "try" && <TrySection models={models} />}
    </div>
  );
}

type Sub = "tiers" | "models" | "providers" | "try";
type Run = (fn: () => Promise<unknown>) => Promise<boolean>;

// ---------------------------------------------------------------- providers

function ProviderSection({ providers, models, run }: { providers: Provider[]; models: Model[]; run: Run }) {
  const [managing, setManaging] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const current = providers.find((p) => p.id === managing);

  return (
    <section>
      <p className="hint">供應商 = 模型從哪裡來（本機 Ollama、OpenAI、OpenRouter、Anthropic…）。按「管理」可以換 API key、列出並註冊模型。</p>
      {providers.map((p) => (
        <div key={p.id} className={`card list-row ${p.enabled ? "" : "disabled"}`}>
          <div className="list-main">
            <div className="row">
              <strong>{p.name}</strong>
              <span className="tag">{p.kind}</span>
              <span className="muted">{models.filter((m) => m.provider_id === p.id).length} 個模型</span>
            </div>
            <div className="muted clamp">
              {p.base_url || "（官方預設端點）"}・{p.has_api_key ? `🔒 ${p.api_key_hint}` : "未設定 API key"}
            </div>
          </div>
          <label className="toggle">
            <input type="checkbox" checked={p.enabled} onChange={(e) => run(() => llm.updateProvider(p.id, { enabled: e.target.checked }))} />
            啟用
          </label>
          <button onClick={() => setManaging(p.id)}>管理</button>
        </div>
      ))}
      <button className="link" onClick={() => setAdding(true)}>
        ＋ 新增供應商
      </button>
      {current && <ProviderModal p={current} models={models} run={run} onClose={() => setManaging(null)} />}
      {adding && <AddProviderModal run={run} onClose={() => setAdding(false)} />}
    </section>
  );
}

function ProviderModal({ p, models, run, onClose }: { p: Provider; models: Model[]; run: Run; onClose: () => void }) {
  const [remote, setRemote] = useState<RemoteModel[] | string | null>(null);
  const [filter, setFilter] = useState("");
  const [editingKey, setEditingKey] = useState(false);
  const registered = models.filter((m) => m.provider_id === p.id);

  const listRemote = async () => {
    setRemote("連線中…");
    try {
      setRemote(await llm.remoteModels(p.id));
    } catch (e) {
      setRemote(`❌ ${(e as Error).message}`);
    }
  };
  const shown = Array.isArray(remote) ? remote.filter((m) => m.id.toLowerCase().includes(filter.trim().toLowerCase())) : [];

  return (
    <Modal title={`管理供應商：${p.name}`} onClose={onClose}>
      <form
        className="field"
        onSubmit={(e) => {
          e.preventDefault();
          const v = (new FormData(e.currentTarget).get("base_url") as string).trim();
          run(() => llm.updateProvider(p.id, { base_url: v }));
        }}
      >
        <span className="field-label">Base URL</span>
        <div className="row">
          <input name="base_url" defaultValue={p.base_url ?? ""} placeholder="留空 = 官方預設端點" style={{ flex: 1 }} />
          <button type="submit">儲存</button>
        </div>
      </form>

      <div className="field">
        <span className="field-label">API key</span>
        <div className="row">
          <span className="muted">{p.has_api_key ? `🔒 ${p.api_key_hint}（已加密）` : "未設定"}</span>
          <button className="link" onClick={() => setEditingKey(!editingKey)}>
            {p.has_api_key ? "更換" : "設定"}
          </button>
          {p.has_api_key && (
            <button className="link danger" onClick={() => confirm("確定清除這個 API key？") && run(() => llm.updateProvider(p.id, { clear_api_key: true }))}>
              清除
            </button>
          )}
        </div>
        {editingKey && (
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault();
              const v = (new FormData(e.currentTarget).get("key") as string).trim();
              if (v) run(() => llm.updateProvider(p.id, { api_key: v })).then((ok) => ok && setEditingKey(false));
            }}
          >
            <input name="key" type="password" autoComplete="new-password" placeholder="新的 API key" style={{ flex: 1 }} />
            <button type="submit">儲存（加密）</button>
          </form>
        )}
      </div>

      <div className="field">
        <span className="field-label">模型</span>
        <div className="row">
          <button onClick={listRemote}>測試連線 / 列出模型</button>
          <span className="muted">已註冊 {registered.length} 個</span>
        </div>
        {typeof remote === "string" && <div className="muted">{remote}</div>}
        {Array.isArray(remote) && (
          <>
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={`搜尋模型（共 ${remote.length} 個），例如 deepseek、glm`} autoFocus />
            <ul className="remote-list scroll">
              {shown.length === 0 && <li className="muted">沒有符合的模型</li>}
              {shown.slice(0, 200).map((m) => (
                <li key={m.id}>
                  <code>{m.id}</code>
                  {m.registered || registered.some((x) => x.model === m.id) ? (
                    <span className="tag ok">已註冊</span>
                  ) : (
                    <button
                      className="link"
                      onClick={() => run(() => llm.createModel({ provider_id: p.id, model: m.id, display_name: m.display_name, context_window: m.context_window }))}
                    >
                      ＋註冊
                    </button>
                  )}
                </li>
              ))}
              {shown.length > 200 && <li className="muted">還有 {shown.length - 200} 個，請輸入關鍵字縮小範圍</li>}
            </ul>
          </>
        )}
      </div>

      <div className="row modal-foot">
        <button
          className="danger"
          onClick={() => confirm(`刪除 ${p.name}？其下 ${registered.length} 個模型也會移除`) && run(() => llm.deleteProvider(p.id)).then((ok) => ok && onClose())}
        >
          刪除供應商
        </button>
      </div>
    </Modal>
  );
}

function AddProviderModal({ run, onClose }: { run: Run; onClose: () => void }) {
  const [kind, setKind] = useState<ProviderKind>("openai_compat");
  const [defaultUrl, setDefaultUrl] = useState(FALLBACK_URL);
  useEffect(() => {
    llm
      .defaults()
      .then((d) => setDefaultUrl((f) => ({ ...f, ...Object.fromEntries(Object.entries(d.base_urls).filter(([, v]) => v)) })))
      .catch(() => {});
  }, []);

  return (
    <Modal title="新增供應商" onClose={onClose}>
      <form
        className="spec-form"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          const api_key = (f.get("api_key") as string).trim();
          run(() =>
            llm.createProvider({
              name: (f.get("name") as string).trim(),
              kind,
              base_url: (f.get("base_url") as string).trim() || undefined,
              api_key: api_key || undefined,
            }),
          ).then((ok) => ok && onClose());
        }}
      >
        <label className="field">
          <span className="field-label">類型</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as ProviderKind)}>
            {(Object.keys(KIND_LABEL) as ProviderKind[]).map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">名稱</span>
          <input name="name" required placeholder="例如 OpenRouter、本機 Ollama" />
        </label>
        <label className="field">
          <span className="field-label">Base URL</span>
          <input name="base_url" key={kind + defaultUrl[kind]} defaultValue={defaultUrl[kind]} placeholder={kind === "anthropic" ? "留空 = 官方端點" : "base URL"} />
          {kind === "openai_compat" && <span className="hint">OpenRouter 填 https://openrouter.ai/api/v1</span>}
        </label>
        <label className="field">
          <span className="field-label">API key</span>
          <input name="api_key" type="password" autoComplete="new-password" placeholder={kind === "ollama" ? "本地通常留空" : "API key"} />
          <span className="hint">以 AES-256-GCM 加密存入資料庫，之後畫面只會顯示遮罩。</span>
        </label>
        <div className="row modal-foot">
          <span style={{ flex: 1 }} />
          <button type="button" className="link" onClick={onClose}>
            取消
          </button>
          <button type="submit">新增</button>
        </div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------- models

const EFFORT_HELP =
  "思考型模型（qwen3.5、gpt-5、DeepSeek…）設 none 可大幅加速，也避免 token 全花在思考上。gpt-5 沒有 none 會自動改用 minimal；一定要思考的模型（GLM 5.3）會自動改用 low；不支援思考的模型（gpt-4o-mini）會自動略過。";

function tierBadges(id: string, tiers: Tiers) {
  return (["large", "small", "vision"] as const)
    .filter((t) => tiers[t].includes(id))
    .map((t) => `${t} #${tiers[t].indexOf(id) + 1}`);
}

function ModelSection({ models, tiers, run, goto }: { models: Model[]; tiers: Tiers; run: Run; goto: (s: Sub) => void }) {
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const q = filter.trim().toLowerCase();
  const shown = models.filter((m) => !q || `${m.model} ${m.provider_name} ${m.display_name ?? ""}`.toLowerCase().includes(q));
  const current = models.find((m) => m.id === editing);

  if (models.length === 0)
    return (
      <p className="hint">
        還沒有註冊模型。到「
        <button className="link" onClick={() => goto("providers")}>
          供應商
        </button>
        」按「管理」→「列出模型」→「＋註冊」。
      </p>
    );

  return (
    <section>
      {models.length > 6 && <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="搜尋已註冊的模型" />}
      {shown.map((m) => (
        <div key={m.id} className={`card list-row ${m.enabled ? "" : "disabled"}`}>
          <div className="list-main">
            <div className="row">
              <code>{m.model}</code>
              {tierBadges(m.id, tiers).map((b) => (
                <span key={b} className="tag ok">
                  {b}
                </span>
              ))}
              {m.vision_verified_at && <span className="tag">👁️ 可看圖</span>}
            </div>
            <div className="muted">
              {m.provider_name}・思考：{m.reasoning_effort ?? "預設"}
              {!m.supports_json_schema && "・不送 JSON schema"}
            </div>
          </div>
          <label className="toggle">
            <input type="checkbox" checked={m.enabled} onChange={(e) => run(() => llm.updateModel(m.id, { enabled: e.target.checked }))} />
            啟用
          </label>
          <button onClick={() => setEditing(m.id)}>設定</button>
        </div>
      ))}
      {current && (
        <Modal title={`模型設定：${current.model}`} onClose={() => setEditing(null)}>
          <div className="muted">
            {current.provider_name}
            {current.context_window ? `・context ${current.context_window.toLocaleString()}` : ""}
            {tierBadges(current.id, tiers).length ? `・在分級 ${tierBadges(current.id, tiers).join("、")}` : "・還沒加入分級"}
          </div>
          <label className="field">
            <span className="field-label">思考強度</span>
            <select
              value={current.reasoning_effort ?? ""}
              onChange={(e) => run(() => llm.updateModel(current.id, { reasoning_effort: (e.target.value || null) as ReasoningEffort | null }))}
            >
              <option value="">預設（由模型決定）</option>
              <option value="none">none（不思考，最快）</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high（最慢、最貴）</option>
            </select>
            <span className="hint">{EFFORT_HELP}</span>
          </label>
          <label className="toggle">
            <input type="checkbox" checked={current.supports_json_schema} onChange={(e) => run(() => llm.updateModel(current.id, { supports_json_schema: e.target.checked }))} />
            支援 JSON schema
          </label>
          <span className="hint">
            村長規劃、工匠、審核員、工具型村民會要求 JSON 輸出。勾選＝送出 JSON schema 約束；取消勾選＝不送約束，改靠說明文字與解析（照樣會使用這個模型）。
            如果這個模型常出現「JSON 輸出被截斷」或「找不到 JSON 物件」（例如有些模型在約束下會一直輸出空白），就取消勾選。
          </span>
          <label className="toggle">
            <input type="checkbox" checked={current.enabled} onChange={(e) => run(() => llm.updateModel(current.id, { enabled: e.target.checked }))} />
            啟用
          </label>
          <VisionTest key={current.id} model={current} run={run} />
          <div className="row modal-foot">
            <button
              className="danger"
              onClick={() => confirm(`移除 ${current.model}？`) && run(() => llm.deleteModel(current.id)).then((ok) => ok && setEditing(null))}
            >
              移除模型
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}

const COLOR_DOT: Record<string, string> = { 紅: "#dc2828", 藍: "#285adc", 綠: "#1e963c", 紫: "#8c3cbe" };

/** 看圖測驗：當場出一張答案已知的圖（四位數字＋彩色圖形），答對才算真的看得到圖 */
function VisionTest({ model, run }: { model: Model; run: Run }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<VisionTestResult | string | null>(null);

  const test = async () => {
    setBusy(true);
    setResult(null);
    await run(async () => {
      try {
        setResult(await llm.visionTest(model.id));
      } catch (e) {
        setResult((e as Error).message);
      }
    });
    setBusy(false);
  };

  return (
    <div className="field">
      <span className="field-label">看圖測驗</span>
      <div className="row">
        <span className={model.vision_verified_at ? "" : "muted"} style={{ flex: 1 }}>
          {model.vision_verified_at ? `👁️ 通過（${new Date(model.vision_verified_at).toLocaleString("zh-TW")}）` : "還沒通過：不能放進 vision 分級"}
        </span>
        <button type="button" disabled={busy} onClick={test}>
          {busy ? "測驗中…" : model.vision_verified_at ? "重新測驗" : "開始測驗"}
        </button>
      </div>
      <span className="hint">
        給模型看一張當場畫的圖，請它說出圖上的四位數字和圖形顏色。兩個都對才算通過（有些模型收到圖片不會報錯，而是忽略圖片亂答）。
        會用掉一次這個模型的呼叫費用。
      </span>
      {typeof result === "string" && <p className="error">{result}</p>}
      {result && typeof result !== "string" && (
        <div className={`card vision-result ${result.passed ? "ok" : "fail"}`}>
          <img src={result.image} alt="測驗圖" />
          <div>
            <strong>{result.passed ? "✅ 通過" : "❌ 沒通過"}</strong>
            <div className="muted">
              正確答案：{result.expected_number}，
              <span className="color-dot" style={{ background: COLOR_DOT[result.expected_color] }} />
              {result.expected_color}色
            </div>
            <div className="clamp">模型回答：{result.answer}</div>
            <div className="hint">{result.reason}</div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- tiers

const TIER_LABEL = {
  large: "large（村長、工匠、困難任務）",
  small: "small（村民日常、備援）",
  vision: "👁️ vision（鑑定師看圖）",
} as const;

function TierSection({ models, tiers, run, goto }: { models: Model[]; tiers: Tiers; run: Run; goto: (s: Sub) => void }) {
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

  if (models.length === 0)
    return (
      <p className="hint">
        還沒有註冊模型。先到「
        <button className="link" onClick={() => goto("providers")}>
          供應商
        </button>
        」新增供應商並註冊模型，再回來排分級。
      </p>
    );

  return (
    <section>
      <p className="hint">村長、工匠用 large；村民依自己的設定用 large 或 small。排在前面的優先使用，失敗依序往下；兩級互為備援。</p>
      <div className="tiers">
        {(["large", "small", "vision"] as const).map((tier) => {
          const pool = tier === "vision" ? models.filter((m) => m.vision_verified_at) : models;
          const untested = tier === "vision" ? models.filter((m) => !m.vision_verified_at).length : 0;
          return (
            <div key={tier} className={`card ${tier === "vision" ? "tier-vision" : ""}`}>
              <strong>{TIER_LABEL[tier]}</strong>
              {tier === "vision" && <span className="hint">鑑定師看圖專用：村長、村民、審核員、書記都不會用到它；也不和 large / small 互相備援。</span>}
              <ol>
                {draft[tier].map((id, i) => (
                  <li key={id}>
                    <code className={byId[id]?.enabled === false ? "muted" : ""}>{byId[id]?.model ?? id}</code>
                    {tier === "vision" && byId[id] && !byId[id].vision_verified_at && <span className="tag warn">測驗沒過，會略過</span>}
                    <button className="link" onClick={() => move(tier, i, -1)}>↑</button>
                    <button className="link" onClick={() => move(tier, i, 1)}>↓</button>
                    <button className="link danger" onClick={() => setDraft((t) => ({ ...t, [tier]: t[tier].filter((x) => x !== id) }))}>✕</button>
                  </li>
                ))}
              </ol>
              <select value="" onChange={(e) => e.target.value && setDraft((t) => ({ ...t, [tier]: [...t[tier], e.target.value] }))}>
                <option value="">{tier === "vision" && pool.length === 0 ? "（還沒有通過看圖測驗的模型）" : "＋加入模型…"}</option>
                {pool
                  .filter((m) => !draft[tier].includes(m.id))
                  .map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.model}（{m.provider_name}）
                    </option>
                  ))}
              </select>
              {tier === "vision" && untested > 0 && (
                <span className="hint">
                  只有通過看圖測驗的模型能加入。到「
                  <button className="link" onClick={() => goto("models")}>
                    模型
                  </button>
                  」按「設定」→「開始測驗」（gpt-4o-mini、gpt-5、Gemini、Claude、Qwen VL 這類模型通常看得到圖）。
                </span>
              )}
            </div>
          );
        })}
      </div>
      <div className="row">
        <button disabled={!dirty} onClick={() => run(() => llm.saveTiers(draft))}>
          儲存分級
        </button>
        {dirty && <span className="hint">● 有未儲存的變更</span>}
      </div>
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
      <p className="hint">直接問模型一句話，確認連得上、回答正常。</p>
      <div className="row">
        <select value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="tier:large">tier: large</option>
          <option value="tier:small">tier: small</option>
          <option value="tier:vision">tier: vision</option>
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
