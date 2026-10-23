import { useState } from "react";
import { residents, villagers, type Resident, type ResidentPreview, type ResidentStatus, type Villager } from "../api";
import { Modal } from "../ui/Modal";
import type { SpriteManifest } from "../village/layout";
import { SpriteThumb } from "./fields";

export const STATUS_LABEL: Record<ResidentStatus, string> = { online: "🟢 在村", offline: "🔴 外出中", unknown: "⚪ 還沒檢查" };

const FILES_HELP =
  "使用者在問答附的文件會整份交給它處理（它用 LLM 整理時，內容也會交給那個 LLM 服務商）；只邀請你信任的服務。";

function AcceptsNote({ accepts }: { accepts?: string[] }) {
  if (!accepts?.length) return null;
  return (
    <div className="muted">
      📎 收文件：{accepts.join("、")}。{FILES_HELP}
    </div>
  );
}

const SHARE_HELP =
  "打開後，有人問答派工給這位駐村專家時，會把「提問者自己的 LLM 設定」（網址、模型、API key）放在 header 交給它，" +
  "讓它用提問者的模型回答。等於把 key 交給另一個服務：只對你信任的專家打開（例如自己架的台灣史專家）。" +
  "關閉時不提供 LLM；需要呼叫端提供 LLM 的專家（例如台灣史專家）就只會回傳找到的資料。";

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("zh-TW", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
}

function SpritePick({ value, onPick, manifest }: { value: string | null; onPick: (id: string) => void; manifest: SpriteManifest | null }) {
  const sprites = Object.entries(manifest?.characters ?? {}).filter(([, c]) => !c.system);
  return (
    <div className="sprite-pick">
      {sprites.map(([id, c]) => (
        <button key={id} type="button" className={value === id ? "picked" : ""} title={c.desc ? `${c.name}：${c.desc}` : c.name} onClick={() => onPick(id)}>
          <SpriteThumb sprite={id} manifest={manifest} />
          <span>{c.name}</span>
        </button>
      ))}
    </div>
  );
}

function CardSummary({ p }: { p: ResidentPreview }) {
  const skills = p.card.skills ?? [];
  return (
    <div className="card resident-card">
      <div className="row">
        <strong>{p.card.name}</strong>
        <span className="tag">{p.online ? STATUS_LABEL.online : STATUS_LABEL.offline}</span>
        {p.card.version && <span className="muted">v{p.card.version}</span>}
      </div>
      <div>{p.card.description}</div>
      {skills.map((s, i) => (
        <div key={i} className="muted">
          ・{s.name ?? s.id}：{s.description}
          {s.examples?.length ? `（例：${s.examples.slice(0, 2).join("、")}）` : ""}
        </div>
      ))}
      <AcceptsNote accepts={p.accepts} />
      <div className="muted">
        問答網址：{p.url}
        {p.a2a_path}
      </div>
      {!p.online && <div className="step-error">目前連不上或還沒準備好：{p.health_error}（還是可以先邀請，恢復後會自動變成「在村」）</div>}
    </div>
  );
}

/** 邀請駐村專家：填網址與存取 token → 讀取名片 → 確認名字、外觀、共享 LLM 開關 → 邀請入村 */
export function InviteResident({ manifest, onClose, onDone }: { manifest: SpriteManifest | null; onClose: () => void; onDone: () => void }) {
  const [url, setUrl] = useState("http://host.docker.internal:");
  const [token, setToken] = useState("");
  const [preview, setPreview] = useState<ResidentPreview | null>(null);
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [sprite, setSprite] = useState<string | null>("scholar");
  const [share, setShare] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const read = async () => {
    setBusy(true);
    setError("");
    try {
      const p = await residents.preview(url.trim());
      setPreview(p);
      setId(p.suggested_id);
      setName(p.card.name);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const invite = async () => {
    if (!preview) return;
    setBusy(true);
    setError("");
    try {
      await residents.invite({ id: id.trim(), url: preview.url, token: token.trim() || undefined, name: name.trim() || undefined, sprite, share_llm: share });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="🧳 邀請駐村專家" onClose={onClose}>
      <p className="hint">駐村專家是在別處獨立部署的 agent（例如 extra-agents 裡的台灣史專家），村子用 A2A 協定呼叫它。</p>
      <label className="field">
        <span className="field-label">網址</span>
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://host.docker.internal:8100" />
        <span className="hint">
          村子在容器裡，連你電腦上的服務要用 <code>host.docker.internal</code>，例如 <code>http://host.docker.internal:8100</code>
          （不能用 localhost）。也可以直接貼 agent card 的網址。
        </span>
      </label>
      <label className="field">
        <span className="field-label">存取 token（對方要求才需要）</span>
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="new-password" placeholder="例如台灣史專家 .env 裡的 TH_ACCESS_TOKENS" />
        <span className="hint">以 AES-256-GCM 加密存入資料庫，之後畫面只會顯示遮罩。</span>
      </label>
      <div className="row">
        <button type="button" onClick={read} disabled={busy || !url.trim()}>
          {busy && !preview ? "讀取中…" : preview ? "重新讀取名片" : "讀取名片"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}

      {preview && (
        <>
          <CardSummary p={preview} />
          <label className="field">
            <span className="field-label">村民 id（英文小寫，建立後不能改）</span>
            <input value={id} onChange={(e) => setId(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">在村子裡的名字</span>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <div className="field">
            <span className="field-label">外觀</span>
            <SpritePick value={sprite} onPick={setSprite} manifest={manifest} />
          </div>
          <label className="toggle">
            <input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} />
            允許把提問者的 LLM 設定交給它
          </label>
          <span className="hint">
            {SHARE_HELP}
            {preview.wants_llm && !share && " ⚠️ 這位專家的名片寫著需要呼叫端提供 LLM。"}
          </span>
          <div className="row modal-foot">
            <span style={{ flex: 1 }} />
            <button type="button" className="link" onClick={onClose}>
              取消
            </button>
            <button type="button" onClick={invite} disabled={busy || !id.trim()}>
              {busy ? "邀請中…" : "邀請入村"}
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}

/** 管理駐村專家：狀態、重新檢查、重新讀取名片、改網址 / token、共享 LLM 開關、名字與外觀、請它離開 */
export function ManageResident({
  villager,
  resident,
  manifest,
  onClose,
  onChanged,
}: {
  villager: Villager;
  resident: Resident;
  manifest: SpriteManifest | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [r, setR] = useState(resident);
  const [url, setUrl] = useState(resident.url);
  const [token, setToken] = useState("");
  const [name, setName] = useState(villager.spec.card.name);
  const [sprite, setSprite] = useState(villager.spec["x-village"].sprite);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const run = async (label: string, fn: () => Promise<Resident | void>, done?: string) => {
    setBusy(label);
    setError("");
    setMsg("");
    try {
      const out = await fn();
      if (out) setR(out);
      if (done) setMsg(done);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  const saveLook = () =>
    run(
      "look",
      async () => {
        const spec = structuredClone(villager.spec);
        spec.card.name = name.trim() || spec.card.name;
        spec["x-village"].sprite = sprite;
        await villagers.update(spec);
      },
      "名字與外觀已更新。",
    );

  return (
    <Modal title={`🧳 駐村專家：${villager.spec.card.name}`} onClose={onClose}>
      <div className="card">
        <div className="row">
          <strong>{STATUS_LABEL[r.status]}</strong>
          <span className="muted">上次檢查 {when(r.checked_at)}（每分鐘自動檢查）</span>
          <span style={{ flex: 1 }} />
          <button type="button" className="link" disabled={!!busy} onClick={() => run("check", () => residents.check(r.id))}>
            {busy === "check" ? "檢查中…" : "立刻檢查"}
          </button>
        </div>
        {r.last_error && <div className="step-error">{r.last_error}</div>}
        <div className="muted">{villager.spec.card.description}</div>
        <AcceptsNote accepts={villager.spec.remote?.accepts} />
        <div className="row">
          <span className="muted">名片讀取於 {when(r.card_fetched_at)}</span>
          <button type="button" className="link" disabled={!!busy} onClick={() => run("refresh", () => residents.refresh(r.id), "已重新讀取名片（說明、技能已更新；名字、外觀保留）。")}>
            {busy === "refresh" ? "讀取中…" : "重新讀取名片"}
          </button>
        </div>
      </div>

      <label className="field">
        <span className="field-label">網址</span>
        <div className="row">
          <input value={url} onChange={(e) => setUrl(e.target.value)} style={{ flex: 1 }} />
          <button type="button" disabled={!!busy || url.trim() === r.url} onClick={() => run("url", () => residents.update(r.id, { url: url.trim() }), "網址已更新。")}>
            儲存
          </button>
        </div>
        <span className="hint">
          問答時呼叫 {r.url}
          {r.a2a_path}
        </span>
      </label>

      <div className="field">
        <span className="field-label">存取 token</span>
        <div className="row">
          <span className="muted">{r.has_token ? `🔒 ${r.token_hint}（已加密）` : "未設定"}</span>
          {r.has_token && (
            <button type="button" className="link danger" onClick={() => confirm("清除這個存取 token？") && run("token", () => residents.update(r.id, { clear_token: true }), "已清除。")}>
              清除
            </button>
          )}
        </div>
        <div className="row">
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="new-password" placeholder="新的 token" style={{ flex: 1 }} />
          <button
            type="button"
            disabled={!!busy || !token.trim()}
            onClick={() =>
              run("token", () => residents.update(r.id, { token: token.trim() }), "token 已更新（加密保存）。").then(() => setToken(""))
            }
          >
            儲存（加密）
          </button>
        </div>
      </div>

      <label className="toggle">
        <input
          type="checkbox"
          checked={r.share_llm}
          disabled={!!busy}
          onChange={(e) => {
            const on = e.target.checked;
            if (on && !confirm(`確定要把提問者的 LLM 設定（含 API key）交給「${villager.spec.card.name}」嗎？只對你信任的服務打開。`)) return;
            run("share", () => residents.update(r.id, { share_llm: on }), on ? "已允許共享 LLM 設定。" : "已關閉共享 LLM 設定。");
          }}
        />
        允許把提問者的 LLM 設定交給它
      </label>
      <span className="hint">{SHARE_HELP}</span>

      <label className="field">
        <span className="field-label">在村子裡的名字</span>
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <div className="field">
        <span className="field-label">外觀</span>
        <SpritePick value={sprite} onPick={setSprite} manifest={manifest} />
      </div>
      <div className="row">
        <button type="button" disabled={!!busy} onClick={saveLook}>
          儲存名字與外觀
        </button>
      </div>

      {msg && <p className="notice">{msg}</p>}
      {error && <p className="error">{error}</p>}
      <div className="row modal-foot">
        <button
          type="button"
          className="danger"
          onClick={() =>
            confirm(`請「${villager.spec.card.name}」離開村子？（只是從村子移除，不會影響對方的服務；加密的 token 會一起刪除）`) &&
            villagers
              .remove(r.id)
              .then(() => {
                onChanged();
                onClose();
              })
              .catch((e) => setError(e.message))
          }
        >
          請它離開村子
        </button>
      </div>
    </Modal>
  );
}
