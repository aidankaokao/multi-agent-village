import type { AgentSpec, Model, ToolInfo } from "../api";
import { BLOCKED, CRAFTSMAN, MAP_H, MAP_W, MAYOR, nearestFree, REVIEWER, SCRIBE, type SpriteManifest } from "../village/VillageScene";
import { Field, ListInput, NumInput, SpriteThumb } from "./fields";

interface Props {
  spec: AgentSpec;
  isNew: boolean;
  models: Model[];
  tools: ToolInfo[];
  manifest: SpriteManifest | null;
  homes: { id: string; name: string; home: [number, number] }[]; // 其他村民的住處，挑位置時參考
  update: (fn: (d: AgentSpec) => void) => void;
}

/** 村民設定表單：與 agent.yaml 一一對應，每個欄位附上「誰會用到它」的說明 */
export function SpecForm({ spec, isNew, models, tools, manifest, homes, update }: Props) {
  const card = spec.card;
  const v = spec["x-village"];
  const sprites = Object.entries(manifest?.characters ?? {}).filter(([, c]) => !c.system);
  const isTool = spec.kind === "tool";
  const tool = tools.find((t) => t.id === spec.tool?.id);

  const setKind = (kind: "prompt" | "tool") => {
    if (kind === spec.kind) return;
    if (kind === "tool" && spec.prompt.system.trim() && !confirm("改成工具型會清空目前的 System prompt（工具型改用「參數整理說明」），確定？")) return;
    update((d) => {
      d.kind = kind;
      if (kind === "tool") {
        d.tool = d.tool ?? { id: tools[0]?.id ?? "calculator", explain: false };
        d.prompt.system = "";
        d.model.temperature = 0;
      } else {
        d.tool = null;
      }
    });
  };

  /** 挑工具：名片、範例、關鍵字還空著的話，先幫忙填上工具的建議 */
  const pickTool = (t: ToolInfo) =>
    update((d) => {
      d.tool = { id: t.id, explain: d.tool?.explain ?? false };
      if (!d.card.description.trim()) d.card.description = t.description;
      if (d.card.skills[0] && !d.card.skills[0].examples.length) d.card.skills[0].examples = [...t.examples];
      if (!d["x-village"].keywords.length) d["x-village"].keywords = [...t.keywords];
    });

  return (
    <div className="spec-form">
      <section>
        <h3>村民類型</h3>
        <div className="kind-pick">
          <button className={!isTool ? "picked" : ""} onClick={() => setKind("prompt")}>
            <strong>💬 對話型</strong>
            <span className="muted">用 LLM 寫作、翻譯、整理、聊天。你寫工作守則（System prompt）。</span>
          </button>
          <button className={isTool ? "picked" : ""} onClick={() => setKind("tool")}>
            <strong>🔧 工具型</strong>
            <span className="muted">用程式精準完成（計算、圖表、日期）。LLM 只負責把請求整理成參數，弱模型也不會算錯。</span>
          </button>
        </div>
        {isTool && (
          <>
            <Field label="使用的工具">
              <div className="tool-pick">
                {tools.map((t) => (
                  <button key={t.id} className={spec.tool?.id === t.id ? "picked" : ""} onClick={() => pickTool(t)}>
                    <strong>{t.name}</strong>
                    <span className="muted">{t.description}</span>
                  </button>
                ))}
              </div>
            </Field>
            <label className="toggle">
              <input
                type="checkbox"
                checked={spec.tool?.explain ?? false}
                onChange={(e) => update((d) => void (d.tool = { id: d.tool?.id ?? "calculator", explain: e.target.checked }))}
              />
              算完後請 LLM 用一句話說明結果（數字仍以工具算的為準，說錯數字就只顯示工具結果）
            </label>
          </>
        )}
      </section>

      <section>
        <h3>① 名片（給村長看）</h3>
        <p className="hint">村長規劃時只看得到這一段，用它決定「該不該派這位村民」。</p>
        <div className="grid2">
          <Field label="名稱" hint="顯示在村子與聊天框">
            <input value={card.name} placeholder="翻譯官" onChange={(e) => update((d) => void (d.card.name = e.target.value))} />
          </Field>
          <Field label="ID" hint={isNew ? "小寫英文、數字、- 或 _；用 @ID 指名；建立後不能改" : "建立後不能修改"}>
            <input
              value={card.id}
              disabled={!isNew}
              placeholder="translator"
              onChange={(e) => update((d) => void (d.card.id = e.target.value.trim().toLowerCase()))}
            />
          </Field>
        </div>
        <Field label="描述" hint="最重要的一欄：寫清楚能做什麼、適合哪些請求（列出使用者常見的說法）。">
          <textarea
            rows={3}
            value={card.description}
            placeholder="中英互譯。適合「翻譯、翻成英文、中翻英、英翻中」等需求，會保留原文的格式與語氣。"
            onChange={(e) => update((d) => void (d.card.description = e.target.value))}
          />
        </Field>
        {card.skills.map((sk, i) => (
          <div key={i} className="card">
            <div className="row">
              <strong>技能 {i + 1}</strong>
              {card.skills.length > 1 && (
                <button className="link danger" onClick={() => update((d) => void d.card.skills.splice(i, 1))}>
                  移除
                </button>
              )}
            </div>
            <div className="grid2">
              <Field label="技能 ID">
                <input value={sk.id} placeholder="translate" onChange={(e) => update((d) => void (d.card.skills[i].id = e.target.value))} />
              </Field>
              <Field label="技能說明">
                <input value={sk.description} placeholder="中英互譯" onChange={(e) => update((d) => void (d.card.skills[i].description = e.target.value))} />
              </Field>
            </div>
            <Field label="請求範例" hint="一行一句使用者可能會說的話。村長規劃時看得到，弱模型特別需要範例。">
              <ListInput
                multiline
                value={sk.examples}
                placeholder={"把這段翻成英文\n這句英文是什麼意思"}
                onChange={(x) => update((d) => void (d.card.skills[i].examples = x))}
              />
            </Field>
          </div>
        ))}
        <button
          className="link"
          onClick={() => update((d) => void d.card.skills.push({ id: `skill${d.card.skills.length + 1}`, description: "", examples: [] }))}
        >
          ＋ 新增技能
        </button>
        <Field label="標籤（選填）">
          <ListInput value={card.tags} placeholder="language、translation" onChange={(x) => update((d) => void (d.card.tags = x))} />
        </Field>
      </section>

      <section>
        <h3>② 村落（外觀、住處、關鍵字）</h3>
        <Field label="外觀">
          <div className="sprite-pick">
            {sprites.map(([id, c]) => (
              <button
                key={id}
                className={v.sprite === id ? "picked" : ""}
                title={c.desc ? `${c.name}：${c.desc}` : c.name}
                onClick={() => update((d) => void (d["x-village"].sprite = id))}
              >
                <SpriteThumb sprite={id} manifest={manifest} />
                <span>{c.name}</span>
              </button>
            ))}
          </div>
        </Field>
        <Field label="住處" hint={v.home ? `第 ${v.home[0]} 欄、第 ${v.home[1]} 列。點地圖換位置。` : "自動分配。點地圖指定位置。"}>
          <HomePicker home={v.home} homes={homes} onPick={(h) => update((d) => void (d["x-village"].home = h))} />
        </Field>
        <Field
          label="關鍵字"
          hint="① LLM 規劃失敗時的備援：請求出現這些詞就派他。② 防止村長多派工作：請求裡一個關鍵字都沒有、也沒人需要他的成果時，這一步會被移除。"
        >
          <ListInput value={v.keywords} placeholder="翻譯、英文、中文、translate" onChange={(x) => update((d) => void (d["x-village"].keywords = x))} />
        </Field>
      </section>

      <section>
        <h3>③ 模型</h3>
        <div className="grid2">
          <Field label="使用模型" hint="每個人用自己 LLM 設定裡的分級。「指定模型」只對你自己有效，其他人問答時會改用這裡的分級">
            <select
              value={spec.model.model_id ?? `tier:${spec.model.tier}`}
              onChange={(e) =>
                update((d) => {
                  const t = e.target.value;
                  if (t.startsWith("tier:")) {
                    d.model.tier = t.slice(5) as "large" | "small";
                    d.model.model_id = null;
                  } else d.model.model_id = t;
                })
              }
            >
              <option value="tier:small">small 分級（快）</option>
              <option value="tier:large">large 分級（強）</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  指定：{m.provider_name} / {m.display_name || m.model}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Temperature" hint="越低越穩定（翻譯 0.2），越高越有創意（寫詩 0.9）；空白＝模型預設">
            <NumInput value={spec.model.temperature} min={0} max={2} step={0.1} placeholder="預設" onChange={(x) => update((d) => void (d.model.temperature = x))} />
          </Field>
          <Field label="max_tokens" hint="單次輸出上限">
            <NumInput value={spec.model.max_tokens} min={16} step={64} onChange={(x) => update((d) => void (d.model.max_tokens = x ?? 1024))} />
          </Field>
        </div>
      </section>

      <section>
        <h3>{isTool ? "④ 參數整理說明（給 LLM 看）" : "④ Prompt（給村民自己的 LLM 看）"}</h3>
        {isTool ? (
          <Field
            label="怎麼把請求整理成工具參數"
            hint={
              <>
                留空＝使用{tool ? `「${tool.name}」` : "工具"}的預設說明（灰字就是預設內容）。想加規則（例如「金額一律算到整數」）再
                <button className="link" onClick={() => update((d) => void (d.prompt.system = tool?.default_prompt ?? ""))}>
                  把預設說明填進來改
                </button>
                。
              </>
            }
          >
            <textarea
              rows={9}
              className="mono"
              value={spec.prompt.system}
              placeholder={tool?.default_prompt}
              onChange={(e) => update((d) => void (d.prompt.system = e.target.value))}
            />
          </Field>
        ) : (
          <Field label="System prompt" hint="村民的人格與工作規則。寫清楚：要做什麼、輸出格式、不要做什麼（例如不要解釋、不要前言）。">
            <textarea
              rows={9}
              className="mono"
              value={spec.prompt.system}
              placeholder={"你是村子裡的翻譯官。\n規則：\n- 原文是中文就翻成英文，是英文就翻成繁體中文。\n- 只輸出譯文，不要解釋、不要前言。\n- 保留原文的換行與格式。"}
              onChange={(e) => update((d) => void (d.prompt.system = e.target.value))}
            />
          </Field>
        )}
        <Field
          label="任務模板"
          hint={
            <>
              每次收到工作時傳給 LLM 的訊息。<code>{"{instruction}"}</code>＝村長給的任務（必填），<code>{"{context}"}</code>＝前一步村民的成果。
            </>
          }
        >
          <textarea rows={3} className="mono" value={spec.prompt.user_template} onChange={(e) => update((d) => void (d.prompt.user_template = e.target.value))} />
        </Field>
      </section>

      <section>
        <h3>⑤ 自動檢查</h3>
        <p className="hint">每次輸出後自動檢查，不合格會把原因告訴村民並重做一次。也會擋下不適當用語。</p>
        <div className="grid2">
          <Field label="字數上限" hint="空白＝不限制">
            <NumInput value={spec.validate.max_chars} min={1} placeholder="不限制" onChange={(x) => update((d) => void (d.validate.max_chars = x))} />
          </Field>
          <label className="toggle">
            <input type="checkbox" checked={spec.validate.must_not_be_empty} onChange={(e) => update((d) => void (d.validate.must_not_be_empty = e.target.checked))} />
            不可為空白
          </label>
        </div>
      </section>
    </div>
  );
}

/** 30 × 20 的迷你地圖：點一下指定住處，看得到其他村民住哪 */
function HomePicker({
  home,
  homes,
  onPick,
}: {
  home: [number, number] | null;
  homes: { id: string; name: string; home: [number, number] }[];
  onPick: (h: [number, number] | null) => void;
}) {
  const others = [MAYOR, CRAFTSMAN, REVIEWER, SCRIBE].map((s) => ({ id: s.id, name: s.name, home: s.home! })).concat(homes);
  return (
    <div className="row" style={{ alignItems: "flex-start" }}>
      <div
        className="home-map"
        style={{ gridTemplateColumns: `repeat(${MAP_W}, 1fr)`, aspectRatio: `${MAP_W} / ${MAP_H}` }}
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const x = Math.floor(((e.clientX - r.left) / r.width) * MAP_W);
          const y = Math.floor(((e.clientY - r.top) / r.height) * MAP_H);
          onPick(nearestFree([Math.min(MAP_W - 2, Math.max(1, x)), Math.min(MAP_H - 2, Math.max(3, y))])); // 點到樹或房子就挪到旁邊
        }}
      >
        {[...BLOCKED].map((k) => {
          const [x, y] = k.split(",").map(Number) as [number, number];
          return <span key={k} className="home-block" style={{ ...dot([x, y]), width: `${100 / MAP_W}%`, height: `${100 / MAP_H}%` }} />;
        })}
        {others.map((o) => (
          <span key={o.id} className="home-dot other" title={o.name} style={dot(o.home)} />
        ))}
        {home && <span className="home-dot me" style={dot(home)} />}
      </div>
      {home && (
        <button className="link" onClick={() => onPick(null)}>
          改為自動
        </button>
      )}
    </div>
  );
}

const dot = ([x, y]: [number, number]) => ({ left: `${((x + 0.5) / MAP_W) * 100}%`, top: `${((y + 0.5) / MAP_H) * 100}%` });
