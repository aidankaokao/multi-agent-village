import { useEffect, useRef, useState } from "react";
import { auth, type AuthStatus, type Me } from "../api";
import { VillageCanvas, type VillageHandle } from "../village/VillageCanvas";
import { DEMO_VILLAGERS, playDemo } from "./demo";

const FEATURES = [
  ["🧓", "村長看懂你的需求，分派給合適的村民"],
  ["🤝", "村民分工接力，互不相關的工作同時進行"],
  ["🧮", "工具型村民用程式計算、畫圖，結果精準"],
  ["🔍", "審核員把關，答案交出前再檢查一次"],
  ["🔨", "跟工匠聊天，就能打造你自己的村民"],
];

/**
 * 登入頁：背景是會動的示範村落（劇本驅動，不呼叫後端），右側是登入 / 註冊。
 * 系統還沒有帳號時顯示「建立管理員帳號」。
 */
export function LoginPage({ status, onDone }: { status: AuthStatus; onDone: (me: Me) => void }) {
  const village = useRef<VillageHandle>(null);
  const [caption, setCaption] = useState("歡迎來到村子 👋");
  const [mode, setMode] = useState<"login" | "register">(status.setup_required ? "register" : "login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // 等 Phaser 場景載入後再開始演
  useEffect(() => {
    let stop = () => {};
    const t = window.setTimeout(() => {
      stop = playDemo((e) => village.current?.dispatch(e), setCaption);
    }, 1200);
    return () => {
      window.clearTimeout(t);
      stop();
    };
  }, []);

  const registering = mode === "register";
  const canRegister = status.setup_required || status.allow_registration;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (registering && password !== password2) return setError("兩次輸入的密碼不一樣");
    setBusy(true);
    try {
      const r = registering ? await auth.register(username, password, displayName) : await auth.login(username, password);
      onDone(r.user);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-stage">
      <div className="map">
        <VillageCanvas ref={village} villagers={DEMO_VILLAGERS} />
      </div>
      <div className="village-name">Multi-Agent Village</div>
      <div className="login-caption" key={caption}>
        {caption}
      </div>

      <div className="login-card">
        <h1>Multi-Agent Village</h1>
        <p className="muted">像素村落裡的 AI 村民，分工合作完成你的任務。</p>
        <ul className="login-features">
          {FEATURES.map(([icon, text]) => (
            <li key={text}>
              <span>{icon}</span> {text}
            </li>
          ))}
        </ul>

        {status.setup_required ? (
          <div className="notice">第一次使用：建立的第一個帳號會是管理員，可以管理村民、模型與其他人的帳號。</div>
        ) : (
          <nav className="subtabs">
            <button className={!registering ? "active" : ""} onClick={() => (setMode("login"), setError(""))}>
              登入
            </button>
            {canRegister && (
              <button className={registering ? "active" : ""} onClick={() => (setMode("register"), setError(""))}>
                註冊
              </button>
            )}
          </nav>
        )}

        <form className="login-form" onSubmit={submit}>
          <label className="field">
            <span className="field-label">帳號</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              required
              placeholder={registering ? "3～32 個字元：英文、數字、_ . -" : ""}
            />
          </label>
          {registering && (
            <label className="field">
              <span className="field-label">顯示名稱（選填）</span>
              <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="村民怎麼稱呼你" maxLength={100} />
            </label>
          )}
          <label className="field">
            <span className="field-label">密碼</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={registering ? "new-password" : "current-password"}
              required
              placeholder={registering ? "至少 8 個字元" : ""}
            />
          </label>
          {registering && (
            <label className="field">
              <span className="field-label">再輸入一次密碼</span>
              <input type="password" value={password2} onChange={(e) => setPassword2(e.target.value)} autoComplete="new-password" required />
            </label>
          )}
          {error && <p className="error">{error}</p>}
          <button type="submit" disabled={busy}>
            {busy ? "請稍候…" : status.setup_required ? "建立管理員帳號並進入村子" : registering ? "註冊並進入村子" : "登入"}
          </button>
          {!registering && <span className="hint">登入後會記住 30 天。忘記密碼請聯絡管理員。</span>}
        </form>
      </div>
    </div>
  );
}
