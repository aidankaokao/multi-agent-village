import { useCallback, useEffect, useState } from "react";
import { auth, rules as rulesApi, users, type Me, type Role, type UserRow, type VillageRules } from "../api";
import { Modal } from "../ui/Modal";

const ROLE: Record<Role, string> = { admin: "管理員", member: "成員" };

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("zh-TW", { year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "從未登入";
}

/** 帳號管理（管理員）：新增、改角色、停用、重設密碼、刪除；開關公開註冊 */
export function AccountsPanel({ me }: { me: Me }) {
  const [list, setList] = useState<UserRow[]>([]);
  const [rules, setRules] = useState<VillageRules | null>(null);
  const [editing, setEditing] = useState<UserRow | "new" | null>(null);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    try {
      const [u, r] = await Promise.all([users.list(), rulesApi.get()]);
      setList(u);
      setRules(r);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const run = async (fn: () => Promise<unknown>) => {
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
      {rules && (
        <div className="card">
          <label className="toggle">
            <input type="checkbox" checked={rules.allow_registration} onChange={(e) => run(() => rulesApi.save({ ...rules, allow_registration: e.target.checked }))} />
            開放註冊
          </label>
          <span className="hint">登入頁可以自己註冊（註冊的是一般成員）。關閉後只能由管理員在這裡新增帳號。</span>
        </div>
      )}
      <div className="row">
        <span className="hint" style={{ flex: 1 }}>
          管理員可以管理村民、村規、LLM 設定與帳號；成員可以問答、看自己的紀錄。
        </span>
        <button onClick={() => setEditing("new")}>＋ 新增帳號</button>
      </div>
      {list.map((u) => (
        <div key={u.id} className={`card list-row ${u.enabled ? "" : "disabled"}`}>
          <div className="list-main">
            <div className="row">
              <strong>{u.display_name}</strong>
              <code className="muted">@{u.username}</code>
              <span className={`tag ${u.role === "admin" ? "ok" : ""}`}>{ROLE[u.role]}</span>
              {u.id === me.id && <span className="tag">你</span>}
              {!u.enabled && <span className="tag warn">已停用</span>}
            </div>
            <div className="muted">最近登入：{when(u.last_login_at)}</div>
          </div>
          <button onClick={() => setEditing(u)}>管理</button>
        </div>
      ))}
      {editing && <UserModal user={editing === "new" ? null : editing} me={me} run={run} onClose={() => setEditing(null)} />}
    </div>
  );
}

function UserModal({
  user,
  me,
  run,
  onClose,
}: {
  user: UserRow | null;
  me: Me;
  run: (fn: () => Promise<unknown>) => Promise<boolean>;
  onClose: () => void;
}) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [role, setRole] = useState<Role>(user?.role ?? "member");
  const [password, setPassword] = useState("");
  const isMe = user?.id === me.id;

  const save = async () => {
    const ok = user
      ? await run(() =>
          users.update(user.id, {
            display_name: displayName,
            ...(isMe ? {} : { role }),
            ...(password ? { password } : {}),
          }),
        )
      : await run(() => users.create({ username, password, display_name: displayName, role }));
    if (ok) onClose();
  };

  return (
    <Modal title={user ? `管理帳號：${user.display_name}` : "新增帳號"} onClose={onClose}>
      {!user && (
        <label className="field">
          <span className="field-label">帳號</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="3～32 個字元：英文、數字、_ . -" autoFocus />
        </label>
      )}
      <label className="field">
        <span className="field-label">顯示名稱</span>
        <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="留空 = 用帳號" />
      </label>
      <label className="field">
        <span className="field-label">角色</span>
        <select value={role} disabled={isMe} onChange={(e) => setRole(e.target.value as Role)}>
          <option value="member">成員：問答、看自己的紀錄</option>
          <option value="admin">管理員：另外可以管理村民、村規、LLM 設定與帳號</option>
        </select>
        {isMe && <span className="hint">不能改自己的角色（避免把自己鎖在外面）</span>}
      </label>
      <label className="field">
        <span className="field-label">{user ? "重設密碼（留空 = 不改）" : "密碼"}</span>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" placeholder="至少 8 個字元" />
        {user && <span className="hint">重設後，這個人所有裝置都需要重新登入。</span>}
      </label>
      <div className="row modal-foot">
        {user && !isMe && (
          <>
            <button
              className="link"
              onClick={() => run(() => users.update(user.id, { enabled: !user.enabled })).then((ok) => ok && onClose())}
            >
              {user.enabled ? "停用帳號" : "重新啟用"}
            </button>
            <button
              className="link danger"
              onClick={() => confirm(`刪除「${user.display_name}」？他的執行紀錄會保留但不再標示是誰`) && run(() => users.remove(user.id)).then((ok) => ok && onClose())}
            >
              刪除
            </button>
          </>
        )}
        <span style={{ flex: 1 }} />
        <button className="link" onClick={onClose}>
          取消
        </button>
        <button onClick={save} disabled={!user && (!username.trim() || !password)}>
          {user ? "儲存" : "建立"}
        </button>
      </div>
    </Modal>
  );
}

/** 改自己的密碼 */
export function PasswordModal({ onClose }: { onClose: () => void }) {
  const [old, setOld] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  return (
    <Modal title="修改密碼" onClose={onClose}>
      {done ? (
        <>
          <p className="notice">密碼已更新。</p>
          <div className="row modal-foot">
            <span style={{ flex: 1 }} />
            <button onClick={onClose}>關閉</button>
          </div>
        </>
      ) : (
        <form
          className="login-form"
          onSubmit={async (e) => {
            e.preventDefault();
            if (pw !== pw2) return setError("兩次輸入的新密碼不一樣");
            try {
              await auth.changePassword(old, pw);
              setDone(true);
            } catch (err) {
              setError((err as Error).message);
            }
          }}
        >
          <label className="field">
            <span className="field-label">目前的密碼</span>
            <input type="password" value={old} onChange={(e) => setOld(e.target.value)} autoComplete="current-password" autoFocus />
          </label>
          <label className="field">
            <span className="field-label">新密碼</span>
            <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" placeholder="至少 8 個字元" />
          </label>
          <label className="field">
            <span className="field-label">再輸入一次新密碼</span>
            <input type="password" value={pw2} onChange={(e) => setPw2(e.target.value)} autoComplete="new-password" />
          </label>
          {error && <p className="error">{error}</p>}
          <button type="submit">更新密碼</button>
        </form>
      )}
    </Modal>
  );
}
