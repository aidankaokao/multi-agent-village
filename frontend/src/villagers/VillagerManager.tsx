import { useCallback, useEffect, useMemo, useState } from "react";
import { llm, residents, villagers, type AgentSpec, type AgentTest, type Model, type Resident, type ToolInfo, type Villager } from "../api";
import { SpriteThumb, useManifest } from "./fields";
import { TestsPanel, TryPanel } from "./Lab";
import { InviteResident, ManageResident, STATUS_LABEL } from "./Residents";
import { SpecForm } from "./SpecForm";
import { NEW_SESSION, Workshop, type WorkshopSession } from "./Workshop";

const BLANK: AgentSpec = {
  card: { id: "", name: "", description: "", version: "0.1.0", skills: [{ id: "main", description: "", examples: [] }], tags: [] },
  "x-village": { sprite: null, home: null, keywords: [] },
  kind: "prompt",
  tool: null,
  model: { tier: "small", model_id: null, temperature: 0.7, max_tokens: 1024 },
  prompt: { system: "", user_template: "任務：{instruction}\n{context}" },
  validate: { must_not_be_empty: true, max_chars: null },
  tests: [],
};

const PAGE_SIZE = 8;

type Kind = "all" | "prompt" | "tool" | "remote" | "disabled";
const KIND_LABEL: Record<Kind, string> = { all: "全部", prompt: "💬 對話型", tool: "🔧 工具型", remote: "🧳 駐村專家", disabled: "停用中" };

function matchKind(v: Villager, kind: Kind): boolean {
  if (kind === "all") return true;
  if (kind === "disabled") return !v.enabled;
  return v.spec.kind === kind;
}

function matchQuery(v: Villager, q: string): boolean {
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const s = v.spec;
  const text = [v.id, s.card.name, s.card.description, ...s.card.tags, ...s.card.skills.map((k) => k.description), ...s["x-village"].keywords]
    .join(" ")
    .toLowerCase();
  return words.every((w) => text.includes(w.replace(/^@/, "")));
}

// saved = null 代表新村民；fromWorkshop：工匠做的草稿，建立前可以回工坊繼續聊
type Editing = { spec: AgentSpec; saved: AgentSpec | null; nonce: number; fromWorkshop?: boolean };

let nonce = 0;
const open = (spec: AgentSpec, saved: AgentSpec | null, fromWorkshop = false): Editing => ({ spec, saved, nonce: ++nonce, fromWorkshop });

export function VillagerManager() {
  const manifest = useManifest();
  const [list, setList] = useState<Villager[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [error, setError] = useState("");
  const [importing, setImporting] = useState(false);
  const [inWorkshop, setInWorkshop] = useState(false);
  const [session, setSession] = useState<WorkshopSession>(NEW_SESSION);
  const [residentList, setResidentList] = useState<Resident[]>([]);
  const [inviting, setInviting] = useState(false);
  const [managing, setManaging] = useState<string | null>(null); // 駐村專家 id
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<Kind>("all");
  const [page, setPage] = useState(0);

  const shown = useMemo(() => list.filter((v) => matchKind(v, kind) && matchQuery(v, q)), [list, kind, q]);
  const pages = Math.max(1, Math.ceil(shown.length / PAGE_SIZE));
  const at = Math.min(page, pages - 1); // 刪除 / 篩選後頁數變少時不會停在空白頁

  const reload = useCallback(async () => {
    try {
      const [v, m, t, r] = await Promise.all([villagers.list(), llm.models(), villagers.tools(), residents.list()]);
      setList(v);
      setModels(m);
      setTools(t);
      setResidentList(r);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  if (editing) {
    return (
      <Editor
        key={editing.nonce}
        initial={editing}
        list={list}
        models={models}
        tools={tools}
        manifest={manifest}
        onClose={() => {
          setEditing(null);
          reload();
        }}
        onSaved={() => {
          if (editing.fromWorkshop) setSession(NEW_SESSION); // 建立完成，工坊這一輪結束
          reload();
        }}
        onAskCraftsman={(spec, saved) => {
          if (session.log.length && !session.base && !confirm("工坊裡還有正在打造的新村民，要放棄那段對話嗎？")) return;
          setSession((s) => ({ ...NEW_SESSION, draft: spec, base: saved, target: s.target }));
          setEditing(null);
          setInWorkshop(true);
        }}
        onBackToWorkshop={
          editing.fromWorkshop
            ? (spec) => {
                setSession((s) => ({ ...s, draft: spec, results: s.draft === spec ? s.results : [] }));
                setEditing(null);
                setInWorkshop(true);
              }
            : undefined
        }
      />
    );
  }

  if (inWorkshop) {
    return (
      <Workshop
        session={session}
        setSession={setSession}
        models={models}
        manifest={manifest}
        onClose={() => setInWorkshop(false)}
        onOpenEditor={(spec) => {
          setInWorkshop(false);
          setEditing(open(spec, session.base, true));
        }}
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
        <button onClick={() => setInWorkshop(true)}>✨ 跟工匠聊聊，打造村民{session.log.length ? "（繼續）" : ""}</button>
        <button className="link" onClick={() => setEditing(open(structuredClone(BLANK), null))}>
          ＋ 從空白開始
        </button>
        <button className="link" onClick={() => setImporting(!importing)}>
          匯入 YAML
        </button>
        <button className="link" onClick={() => setInviting(true)} title="把在別處獨立部署的 agent（例如台灣史專家）接進村子">
          🧳 邀請駐村專家
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

      {list.length > 0 && (
        <div className="row villager-filter">
          <input
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(0);
            }}
            placeholder={`搜尋名字、@id、說明、關鍵字（共 ${list.length} 位）`}
          />
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as Kind);
              setPage(0);
            }}
          >
            {Object.entries(KIND_LABEL).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </div>
      )}

      {shown.slice(at * PAGE_SIZE, (at + 1) * PAGE_SIZE).map((v) => {
        const s = v.spec;
        const res = s.kind === "remote" ? residentList.find((r) => r.id === v.id) : undefined;
        const openIt = () => (s.kind === "remote" ? setManaging(v.id) : setEditing(open(s, s)));
        return (
          <div key={v.id} className={`card villager-row ${v.enabled ? "" : "disabled"}`}>
            <SpriteThumb sprite={s["x-village"].sprite} manifest={manifest} />
            <div className="villager-info" onClick={openIt}>
              <div className="row">
                <strong>{s.card.name}</strong>
                <code className="muted">@{v.id}</code>
                {s.kind === "remote" && <span className="tag">🧳 駐村專家</span>}
                {res && <span className={`tag ${res.status === "online" ? "ok" : "warn"}`}>{STATUS_LABEL[res.status]}</span>}
                {v.builtin && <span className="tag">內建範例</span>}
                {!v.enabled && <span className="tag warn">停用中</span>}
                {s.kind === "tool" && <span className="tag">🔧 {tools.find((t) => t.id === s.tool?.id)?.name ?? s.tool?.id}</span>}
                {s.tests.length > 0 && <span className="tag">{s.tests.length} 題測試</span>}
              </div>
              <div className="muted clamp">{s.card.description}</div>
            </div>
            <div className="villager-actions">
              <button onClick={openIt}>{s.kind === "remote" ? "管理" : "編輯"}</button>
              {s.kind !== "remote" && (
                <button className="link" onClick={() => setEditing(open(copyOf(s), null))}>
                  複製
                </button>
              )}
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
      {list.length === 0 && <p className="hint">村裡還沒有村民，按「✨ 跟工匠聊聊」開始。</p>}
      {list.length > 0 && shown.length === 0 && <p className="hint">沒有符合的村民。</p>}
      {pages > 1 && (
        <div className="row pager">
          <button type="button" className="link" disabled={at === 0} onClick={() => setPage(at - 1)}>
            ← 上一頁
          </button>
          <span className="muted">
            第 {at + 1} / {pages} 頁（{shown.length} 位）
          </span>
          <button type="button" className="link" disabled={at >= pages - 1} onClick={() => setPage(at + 1)}>
            下一頁 →
          </button>
        </div>
      )}
      {inviting && (
        <InviteResident
          manifest={manifest}
          onClose={() => setInviting(false)}
          onDone={() => {
            setInviting(false);
            reload();
          }}
        />
      )}
      {managing &&
        (() => {
          const v = list.find((x) => x.id === managing);
          const r = residentList.find((x) => x.id === managing);
          return v && r ? (
            <ManageResident key={managing} villager={v} resident={r} manifest={manifest} onClose={() => setManaging(null)} onChanged={reload} />
          ) : null;
        })()}
    </div>
  );
}

// ---------------------------------------------------------------- 編輯器

type SubTab = "spec" | "tests" | "try";

function Editor({
  initial,
  list,
  models,
  tools,
  manifest,
  onClose,
  onSaved,
  onBackToWorkshop,
  onAskCraftsman,
}: {
  initial: Editing;
  list: Villager[];
  models: Model[];
  tools: ToolInfo[];
  manifest: ReturnType<typeof useManifest>;
  onClose: () => void;
  onSaved: () => void;
  onBackToWorkshop?: (spec: AgentSpec) => void;
  onAskCraftsman: (spec: AgentSpec, saved: AgentSpec) => void;
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
        {onBackToWorkshop && (isNew || dirty) ? (
          <button className="link" onClick={() => onBackToWorkshop(spec)} title="編輯的內容會一起帶回去">
            ← 回工坊
          </button>
        ) : (
          <button className="link" onClick={close}>
            ← 村民列表
          </button>
        )}
        <SpriteThumb sprite={spec["x-village"].sprite} manifest={manifest} />
        <div>
          <strong>{spec.card.name || "（未命名）"}</strong> <code className="muted">@{spec.card.id || "?"}</code>
          <div className="muted">{isNew ? "新村民（尚未建立）" : dirty ? "● 有未儲存的變更" : "已儲存"}</div>
        </div>
        <span style={{ flex: 1 }} />
        {!isNew && !onBackToWorkshop && (
          <button className="link" onClick={() => onAskCraftsman(spec, saved!)} title="用聊天的方式說哪裡要改">
            🔨 請工匠幫忙改
          </button>
        )}
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

      {sub === "spec" && <SpecForm spec={spec} isNew={isNew} models={models} tools={tools} manifest={manifest} homes={homes} update={update} />}
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
