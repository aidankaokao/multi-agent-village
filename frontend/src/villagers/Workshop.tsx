import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { villagers, workshop, type AgentSpec, type Model, type TryResult, type WorkshopBody } from "../api";
import { BLOCKED, type SpriteManifest } from "../village/VillageScene";
import { SpriteThumb } from "./fields";
import { TargetSelect } from "./Lab";

interface Entry {
  role: "user" | "assistant";
  content: string;
  note?: boolean; // 過程紀錄：只顯示，不傳給工匠
}

/** 工坊的進度放在上層，切到編輯器再回來時對話還在 */
export interface WorkshopSession {
  log: Entry[];
  draft: AgentSpec | null;
  base: AgentSpec | null; // 修改已建立的村民時，這是目前存在 DB 的版本

  ready: boolean;
  target: string;
  results: (TryResult | "running" | undefined)[];
}

export const NEW_SESSION: WorkshopSession = { log: [], draft: null, base: null, ready: false, target: "", results: [] };

const GREETING = "嗨！我是工匠，專門幫村子打造新村民。\n跟我說說你想要什麼樣的村民？一句話就可以，例如：";
const IDEAS = ["一個幫我整理會議重點的人", "會推薦今天晚餐吃什麼的人", "幫我把文章改得更口語的人"];

const PERSONALITY = (t: number | null) => (t == null ? "模型預設" : t <= 0.3 ? "穩定一致" : t >= 0.8 ? "有創意、每次不同" : "平衡");

export function Workshop({
  session,
  setSession,
  models,
  manifest,
  onClose,
  onOpenEditor,
}: {
  session: WorkshopSession;
  setSession: Dispatch<SetStateAction<WorkshopSession>>;
  models: Model[];
  manifest: SpriteManifest | null;
  onClose: () => void;
  onOpenEditor: (spec: AgentSpec) => void;
}) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState<null | "interview" | "generate">(null);
  const [error, setError] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  const { log, draft, ready, base } = session;
  const hasUser = log.some((e) => e.role === "user");

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [log.length, busy]);

  const body = (entries: Entry[], d: AgentSpec | null = draft): WorkshopBody => ({
    messages: entries.filter((e) => !e.note).map(({ role, content }) => ({ role, content })),
    sprites: Object.entries(manifest?.characters ?? {})
      .filter(([, c]) => !c.system)
      .map(([id, c]) => ({ id, name: c.name, desc: c.desc ?? "" })),
    draft: d,
    target: session.target || null,
    blocked: [...BLOCKED].map((k) => k.split(",").map(Number) as [number, number]),
  });

  const send = async (text = input.trim()) => {
    if (!text || busy) return;
    setInput("");
    setError("");
    const entries = [...log, { role: "user" as const, content: text }];
    setSession((s) => ({ ...s, log: entries }));
    setBusy("interview");
    try {
      const r = await workshop.interview(body(entries));
      setSession((s) => ({ ...s, ready: r.ready, log: [...s.log, { role: "assistant", content: r.reply }] }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const runTests = async (spec: AgentSpec) => {
    for (let i = 0; i < spec.tests.length; i++) {
      setSession((s) => ({ ...s, results: Object.assign([...s.results], { [i]: "running" }) }));
      const t = spec.tests[i];
      const r = await villagers.try({ spec, instruction: t.input, context: t.context, expect: t.expect }).catch(() => undefined);
      setSession((s) => (s.draft === spec ? { ...s, results: Object.assign([...s.results], { [i]: r }) } : s));
    }
  };

  const build = async () => {
    if (busy) return;
    setError("");
    setBusy("generate");
    try {
      const r = await workshop.generate(body(log));
      const notes: Entry[] = r.notes.map((n) => ({ role: "assistant", content: n, note: true }));
      setSession((s) => ({
        ...s,
        draft: r.spec,
        ready: false,
        results: [],
        log: [...s.log, ...notes, { role: "assistant", content: r.summary }],
      }));
      if (r.spec.tests.length) runTests(r.spec);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="panel-body workshop">
      <div className="editor-head">
        <button className="link" onClick={onClose}>
          ← 村民列表
        </button>
        <SpriteThumb sprite="craftsman" manifest={manifest} />
        <div>
          <strong>村民工坊{base ? `：修改「${base.card.name}」` : ""}</strong>
          <div className="muted">{base ? "跟工匠說哪裡要改，他會改一版給你看" : "跟工匠聊聊，他會幫你做出第一版"}</div>
        </div>
        <span style={{ flex: 1 }} />
        {(hasUser || draft) && (
          <button
            className="link danger"
            onClick={() => {
              if (confirm("清空對話和草稿，重新開始？"))
                setSession(base ? { ...NEW_SESSION, draft: base, base, target: session.target } : { ...NEW_SESSION, target: session.target });
            }}
          >
            重新開始
          </button>
        )}
      </div>

      <div className="ws-log" ref={logRef}>
        <div className="ws-msg craftsman">
          <SpriteThumb sprite="craftsman" manifest={manifest} scale={1} />
          <div className="bubble village">
            {base
              ? `要幫「${base.card.name}」改什麼呢？直接說哪裡不滿意就好，例如：「回答常常太長」「語氣太生硬」「不要用條列」。\n改好後一樣會先給你看草稿，你按「儲存」才會生效。`
              : GREETING}
            {!hasUser &&
              !base &&
              IDEAS.map((x) => (
                <button key={x} className="example" onClick={() => send(x)}>
                  {x}
                </button>
              ))}
          </div>
        </div>
        {log.map((e, i) =>
          e.note ? (
            <div key={i} className="muted ws-note">
              ↻ {e.content}
            </div>
          ) : e.role === "user" ? (
            <div key={i} className="bubble user">
              {e.content}
            </div>
          ) : (
            <div key={i} className="ws-msg craftsman">
              <SpriteThumb sprite="craftsman" manifest={manifest} scale={1} />
              <div className="bubble village">{e.content}</div>
            </div>
          ),
        )}
        {busy && (
          <div className="status-live">
            {busy === "interview" ? "工匠想了一下…" : "工匠正在打造村民…（雲端模型約 10～60 秒）"}
          </div>
        )}
      </div>

      {error && (
        <p className="error" onClick={() => setError("")}>
          {error}（點擊關閉）
        </p>
      )}

      <div className="chat-input">
        <textarea
          rows={2}
          value={input}
          placeholder={draft ? "還想改什麼？例如：語氣再活潑一點、不要用條列" : "回答工匠，或描述你想要的村民"}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
        />
        <div className="row">
          <button className="link" onClick={() => send()} disabled={!!busy || !input.trim()}>
            送出（Enter）
          </button>
          <TargetSelect value={session.target} onChange={(t) => setSession((s) => ({ ...s, target: t }))} models={models} defaultLabel="工匠用 large 分級" />
          <span style={{ flex: 1 }} />
          <span className="hint">{ready ? "工匠覺得可以動手了 →" : hasUser ? "資訊不夠也沒關係，隨時可以先做一版" : ""}</span>
          <button className={ready ? "glow" : ""} onClick={build} disabled={!!busy || !hasUser}>
            🔨 {base ? "照對話修改" : draft ? "照對話重新打造" : "打造村民"}
          </button>
        </div>
      </div>

      {draft && (base === null || JSON.stringify(draft) !== JSON.stringify(base)) && (
        <DraftCard spec={draft} isEdit={!!base} results={session.results} manifest={manifest} onRetest={() => runTests(draft)} onOpen={() => onOpenEditor(draft)} />
      )}
    </div>
  );
}

function DraftCard({
  spec,
  isEdit,
  results,
  manifest,
  onRetest,
  onOpen,
}: {
  spec: AgentSpec;
  isEdit: boolean;
  results: WorkshopSession["results"];
  manifest: SpriteManifest | null;
  onRetest: () => void;
  onOpen: () => void;
}) {
  const v = spec["x-village"];
  const examples = spec.card.skills.flatMap((s) => s.examples);
  const done = results.filter((r) => r && r !== "running") as TryResult[];
  const running = results.some((r) => r === "running");
  const passed = done.filter((r) => r.ok).length;

  return (
    <div className="card draft-card">
      <div className="row">
        <SpriteThumb sprite={v.sprite} manifest={manifest} />
        <div>
          <strong>{spec.card.name}</strong> <code className="muted">@{spec.card.id}</code>
          <div className="muted">{isEdit ? "修改後的草稿，還沒儲存" : "草稿，還沒建立"}</div>
        </div>
        <span style={{ flex: 1 }} />
        <button onClick={onOpen} disabled={running}>
          打開編輯器，確認後{isEdit ? "儲存" : "建立"} →
        </button>
      </div>
      <div>{spec.card.description}</div>
      <div className="grid2">
        <div>
          <div className="field-label">他聽得懂這些話</div>
          {examples.map((x) => (
            <div key={x} className="muted">
              「{x}」
            </div>
          ))}
        </div>
        <div>
          <div className="field-label">聽到這些詞會被派出來</div>
          <div className="row">
            {v.keywords.length ? (
              v.keywords.map((k) => (
                <span key={k} className="tag">
                  {k}
                </span>
              ))
            ) : (
              <span className="muted">（還沒有，請在編輯器補上）</span>
            )}
          </div>
          <div className="field-label">個性 / 長度</div>
          <div className="muted">
            {PERSONALITY(spec.model.temperature)}／{spec.validate.max_chars ? `${spec.validate.max_chars} 字以內` : "不限"}
          </div>
        </div>
      </div>
      <details>
        <summary>工作守則（system prompt）</summary>
        <pre className="output">{spec.prompt.system}</pre>
      </details>

      {spec.tests.length > 0 && (
        <div>
          <div className="row">
            <div className="field-label">工匠出的測試題</div>
            {done.length > 0 && !running && (
              <span className={passed === spec.tests.length ? "tag ok" : "tag warn"}>
                {passed}/{spec.tests.length} 通過
              </span>
            )}
            <span style={{ flex: 1 }} />
            <button className="link" onClick={onRetest} disabled={running}>
              {running ? "測試中…" : "重跑測試"}
            </button>
          </div>
          {spec.tests.map((t, i) => {
            const r = results[i];
            return (
              <div key={i} className="ws-test">
                <span>{r === "running" ? "⏳" : r ? (r.ok ? "✅" : "❌") : "・"}</span>
                <div>
                  <div>{t.input}</div>
                  {r && r !== "running" && (
                    <>
                      {r.failures.map((f) => (
                        <div key={f} className="step-error">
                          ✗ {f}
                        </div>
                      ))}
                      {r.error && <div className="step-error">{r.error}</div>}
                      <details>
                        <summary className="muted">看他的回答</summary>
                        <pre className="output">{r.output}</pre>
                      </details>
                    </>
                  )}
                </div>
              </div>
            );
          })}
          <p className="hint">測試用村民自己的模型跑。沒通過的話，可以直接跟工匠說哪裡不好，或到編輯器調整。</p>
        </div>
      )}
    </div>
  );
}
