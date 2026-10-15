import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, getAdminToken, llm, setAdminToken, villagers, type AgentSpec, type AgentTest, type Model, type Villager } from "../api";
import { SpriteThumb, useManifest } from "./fields";
import { TestsPanel, TryPanel } from "./Lab";
import { SpecForm } from "./SpecForm";

const BLANK: AgentSpec = {
  card: { id: "", name: "", description: "", version: "0.1.0", skills: [{ id: "main", description: "", examples: [] }], tags: [] },
  "x-village": { sprite: null, home: null, keywords: [] },
  kind: "prompt",
  model: { tier: "small", model_id: null, temperature: 0.7, max_tokens: 1024 },
  prompt: { system: "", user_template: "任務：{instruction}\n{context}" },
  validate: { must_not_be_empty: true, max_chars: null },
  tests: [],
};

type Editing = { spec: AgentSpec; saved: AgentSpec | null; nonce: number }; // saved = null 代表新村民

let nonce = 0;
const open = (spec: AgentSpec, saved: AgentSpec | null): Editing => ({ spec, saved, nonce: ++nonce });

export function VillagerManager() {
  const manifest = useManifest();
  const [list, setList] = useState<Villager[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [needToken, setNeedToken] = useState(false);
  const [error, setError] = useState("");
  const [importing, setImporting] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [v, m] = await Promise.all([villagers.list(), llm.models()]);
      setList(v);
      setModels(m);
      setNeedToken(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setNeedToken(true);
      else setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  if (needToken) return <TokenGate onDone={reload} />;

  if (editing) {
    return (
      <Editor
        key={editing.nonce}
        initial={editing}
        list={list}
        models={models}
        manifest={manifest}
        onClose={() => {
          setEditing(null);
          reload();
        }}
        onSaved={reload}
      />
    );
  }

  const copyOf = (s: AgentSpec): AgentSpec => {
    const c = structuredClone(s);
    c.card.id = `${s.card.id}-copy`;
    c.card.name = `${s.card.name}（複製）`;
    c["x-village"].home = null;
    return c;
  };

  return (
    <div className="panel-body">
      {error && (
        <p className="error" onClick={() => setError("")}>
          {error}（點擊關閉）
        </p>
      )}
      <div className="row">
        <button onClick={() => setEditing(open(structuredClone(BLANK), null))}>＋ 新增村民</button>
        <button className="link" onClick={() => setImporting(!importing)}>
          匯入 YAML
        </button>
        <span style={{ flex: 1 }} />
        <button
          className="link"
          title="把刪掉的內建範例（詩人、笑話師…）加回來"
          onClick={async () => {
            const r = await villagers.restoreBuiltin().catch((e) => setError(e.message));
            if (r) setError(r.restored.length ? "" : "內建範例都還在，沒有需要還原的");
            reload();
          }}
        >
          還原內建範例
        </button>
      </div>

      {importing && (
        <form
          className="card"
          onSubmit={async (e) => {
            e.preventDefault();
            const text = new FormData(e.currentTarget).get("yaml") as string;
            try {
              const { spec } = await villagers.fromYaml(text);
              setImporting(false);
              setEditing(open(spec, null));
            } catch (err) {
              setError((err as Error).message);
            }
          }}
        >
          <textarea name="yaml" rows={8} className="mono" placeholder="貼上匯出的 YAML（agent.yaml 格式）" />
          <div className="row">
            <button type="submit">載入到編輯器</button>
            <span className="hint">載入後確認內容，按「建立」才會儲存</span>
          </div>
        </form>
      )}

      {list.map((v) => {
        const s = v.spec;
        return (
          <div key={v.id} className={`card villager-row ${v.enabled ? "" : "disabled"}`}>
            <SpriteThumb sprite={s["x-village"].sprite} manifest={manifest} />
            <div className="villager-info" onClick={() => setEditing(open(s, s))}>
              <div className="row">
                <strong>{s.card.name}</strong>
                <code className="muted">@{v.id}</code>
                {v.builtin && <span className="tag">內建範例</span>}
                {!v.enabled && <span className="tag warn">停用中</span>}
                {s.tests.length > 0 && <span className="tag">{s.tests.length} 題測試</span>}
              </div>
              <div className="muted clamp">{s.card.description}</div>
            </div>
            <div className="villager-actions">
              <button onClick={() => setEditing(open(s, s))}>編輯</button>
              <button className="link" onClick={() => setEditing(open(copyOf(s), null))}>
                複製
              </button>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={v.enabled}
                  onChange={(e) =>
                    villagers
                      .setEnabled(v.id, e.target.checked)
                      .then(reload)
                      .catch((err) => setError(err.message))
                  }
                />
                啟用
              </label>
            </div>
          </div>
        );
      })}
      {list.length === 0 && <p className="hint">村裡還沒有村民，按「＋ 新增村民」開始。</p>}
    </div>
  );
}

// ---------------------------------------------------------------- 編輯器

type SubTab = "spec" | "tests" | "try";

function Editor({
  initial,
  list,
  models,
  manifest,
  onClose,
  onSaved,
}: {
  initial: Editing;
  list: Villager[];
  models: Model[];
  manifest: ReturnType<typeof useManifest>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [spec, setSpec] = useState<AgentSpec>(() => structuredClone(initial.spec));
  const [saved, setSaved] = useState<AgentSpec | null>(initial.saved);
  const [sub, setSub] = useState<SubTab>("spec");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [yamlText, setYamlText] = useState<string | null>(null);
  const isNew = saved === null;
  const row = list.find((v) => v.id === spec.card.id && !isNew);
  const dirty = useMemo(() => JSON.stringify(spec) !== JSON.stringify(saved), [spec, saved]);

  const update = (fn: (d: AgentSpec) => void) =>
    setSpec((s) => {
      const d = structuredClone(s);
      fn(d);
      return d;
    });

  const homes = list
    .filter((v) => v.id !== spec.card.id && v.enabled && v.spec["x-village"].home)
    .map((v) => ({ id: v.id, name: v.spec.card.name, home: v.spec["x-village"].home! }));

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const v = isNew ? await villagers.create(spec) : await villagers.update(spec);
      setSaved(v.spec);
      setSpec(structuredClone(v.spec));
      setNotice(isNew ? `已建立「${v.spec.card.name}」，村子裡看得到他了` : "已儲存，問答會立刻使用新設定");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const close = () => {
    if (dirty && !confirm("有未儲存的變更，確定離開？")) return;
    onClose();
  };

  const addTest = (t: AgentTest) => {
    update((d) => void d.tests.push(t));
    setSub("tests");
  };

  return (
    <div className="panel-body editor">
      <div className="editor-head">
        <button className="link" onClick={close}>
          ← 村民列表
        </button>
        <SpriteThumb sprite={spec["x-village"].sprite} manifest={manifest} />
        <div>
          <strong>{spec.card.name || "（未命名）"}</strong> <code className="muted">@{spec.card.id || "?"}</code>
          <div className="muted">{isNew ? "新村民（尚未建立）" : dirty ? "● 有未儲存的變更" : "已儲存"}</div>
        </div>
        <span style={{ flex: 1 }} />
        <button onClick={save} disabled={busy || (!dirty && !isNew)}>
          {busy ? "儲存中…" : isNew ? "建立" : "儲存"}
        </button>
      </div>

      {error && (
        <p className="error" onClick={() => setError("")}>
          {error}（點擊關閉）
        </p>
      )}
      {notice && !error && (
        <p className="notice" onClick={() => setNotice("")}>
          {notice}
        </p>
      )}

      <nav className="subtabs">
        <button className={sub === "spec" ? "active" : ""} onClick={() => setSub("spec")}>
          設定
        </button>
        <button className={sub === "try" ? "active" : ""} onClick={() => setSub("try")}>
          試打
        </button>
        <button className={sub === "tests" ? "active" : ""} onClick={() => setSub("tests")}>
          測試題（{spec.tests.length}）
        </button>
      </nav>

      {sub === "spec" && <SpecForm spec={spec} isNew={isNew} models={models} manifest={manifest} homes={homes} update={update} />}
      {sub === "try" && <TryPanel spec={spec} models={models} onAddTest={addTest} />}
      {sub === "tests" && <TestsPanel spec={spec} models={models} update={update} />}

      <section className="editor-foot">
        <div className="row">
          <button
            className="link"
            onClick={async () => {
              try {
                setYamlText(yamlText === null ? (await villagers.toYaml(spec)).yaml : null);
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            {yamlText === null ? "匯出 YAML" : "隱藏 YAML"}
          </button>
          {row && (
            <button
              className="link danger"
              onClick={async () => {
                if (!confirm(`確定刪除「${spec.card.name}」？${row.builtin ? "（內建範例可以用「還原內建範例」救回）" : "（無法復原）"}`)) return;
                try {
                  await villagers.remove(row.id);
                  onClose();
                } catch (e) {
                  setError((e as Error).message);
                }
              }}
            >
              刪除村民
            </button>
          )}
        </div>
        {yamlText !== null && (
          <>
            <textarea className="mono" rows={12} readOnly value={yamlText} />
            <button className="link" onClick={() => navigator.clipboard?.writeText(yamlText)}>
              複製到剪貼簿
            </button>
          </>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- admin token

function TokenGate({ onDone }: { onDone: () => void }) {
  return (
    <div className="panel-body">
      <h3>管理員驗證</h3>
      <p className="hint">
        後端設定了 <code>VILLAGE_ADMIN_TOKEN</code>，請輸入 .env 裡的同一個值。
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setAdminToken((new FormData(e.currentTarget).get("token") as string).trim());
          onDone();
        }}
      >
        <input name="token" type="password" autoComplete="off" placeholder="admin token" defaultValue={getAdminToken()} />
        <button type="submit">確認</button>
      </form>
    </div>
  );
}
