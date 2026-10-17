"""Phase 5A：帳號、登入 session、權限、事件只給提問者、資料庫遷移。"""

import sqlite3

import httpx
import pytest

from test_villagers import FakeClient
from village.config import REPO_ROOT, Settings
from village.events import for_viewer
from village.main import create_app
from village.security import auth


@pytest.fixture
async def app(tmp_path, monkeypatch):
    for k in ("VILLAGE_MASTER_KEYS", "VILLAGE_MASTER_KEY"):
        monkeypatch.delenv(k, raising=False)
    auth.throttle._fails.clear()
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/v.db", config_dir=tmp_path / "cfg", agents_dir=REPO_ROOT / "agents")
    a = create_app(settings, client_factory=FakeClient, handoff_delay=0)
    async with a.router.lifespan_context(a):
        yield a, tmp_path / "v.db"


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_first_user_is_admin_then_members(app):
    a, db = app
    async with client(a) as admin, client(a) as bob, client(a) as anon:
        st = (await anon.get("/api/auth/status")).json()
        assert st == {"setup_required": True, "allow_registration": True, "user": None, "llm_ready": False, "vision_ready": False, "max_images": 3}

        r = await admin.post("/api/auth/register", json={"username": "Aidan", "password": "password123", "display_name": "艾登"})
        assert r.status_code == 200 and r.json()["first"] and r.json()["user"]["role"] == "admin"
        assert r.json()["user"]["username"] == "aidan"  # 帳號一律小寫
        assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
        token = r.cookies[auth.COOKIE]
        stored = sqlite3.connect(db).execute("select token_hash from auth_sessions").fetchone()[0]
        assert token not in stored and len(stored) == 64  # DB 只存 hash

        r = await bob.post("/api/auth/register", json={"username": "bob", "password": "password123"})
        assert r.json()["user"]["role"] == "member" and not r.json()["first"]
        assert (await bob.post("/api/auth/register", json={"username": "bob", "password": "password123"})).status_code == 409

        # 一般成員：可以問答、看自己的紀錄、管理自己的 LLM；不能碰全村的設定
        assert (await bob.get("/api/agents")).status_code == 200
        assert (await bob.get("/api/runs")).status_code == 200
        assert (await bob.get("/api/llm/providers")).json() == []
        assert (await anon.get("/api/llm/providers")).status_code == 401
        for path in ("/api/villagers", "/api/rules", "/api/users"):
            assert (await bob.get(path)).status_code == 403, path
            assert (await anon.get(path)).status_code == 401, path
        assert (await admin.get("/api/users")).status_code == 200

        # 關閉註冊
        rules = (await admin.get("/api/rules")).json()
        await admin.put("/api/rules", json={**rules, "allow_registration": False})
        assert (await anon.get("/api/auth/status")).json()["allow_registration"] is False
        assert (await anon.post("/api/auth/register", json={"username": "eve", "password": "password123"})).status_code == 403


async def test_login_logout_password_and_throttle(app):
    a, _ = app
    async with client(a) as c:
        await c.post("/api/auth/register", json={"username": "admin", "password": "password123"})
        await c.post("/api/auth/logout")
        assert (await c.get("/api/auth/status")).json()["user"] is None
        assert (await c.get("/api/agents")).status_code == 401

        assert (await c.post("/api/auth/login", json={"username": "admin", "password": "wrong-pass"})).status_code == 401
        assert (await c.post("/api/auth/login", json={"username": "nobody", "password": "wrong-pass"})).status_code == 401
        r = await c.post("/api/auth/login", json={"username": "ADMIN", "password": "password123"})
        assert r.status_code == 200 and (await c.get("/api/agents")).status_code == 200

        assert (await c.post("/api/auth/password", json={"old_password": "nope", "new_password": "newpass123"})).status_code == 422
        assert (await c.post("/api/auth/password", json={"old_password": "password123", "new_password": "short"})).status_code == 422
        assert (await c.post("/api/auth/password", json={"old_password": "password123", "new_password": "newpass123"})).status_code == 204

        for _ in range(5):
            await c.post("/api/auth/login", json={"username": "admin", "password": "bad-guess"})
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "newpass123"})
        assert r.status_code == 429  # 連續失敗被鎖

    for bad in ({"username": "ab", "password": "password123"}, {"username": "a b c", "password": "password123"}, {"username": "abc", "password": "1234567"}):
        async with client(a) as c:
            assert (await c.post("/api/auth/register", json=bad)).status_code == 422


async def test_admin_user_management_guards(app):
    a, _ = app
    async with client(a) as admin, client(a) as bob:
        me = (await admin.post("/api/auth/register", json={"username": "admin", "password": "password123"})).json()["user"]
        r = await admin.post("/api/users", json={"username": "carol", "password": "password123", "role": "member"})
        assert r.status_code == 201
        carol = r.json()
        # 最後一位管理員不能降級 / 停用 / 刪除自己
        assert (await admin.patch(f"/api/users/{me['id']}", json={"role": "member"})).status_code == 422
        assert (await admin.delete(f"/api/users/{me['id']}")).status_code == 422
        # 停用 → 該帳號所有 session 失效
        await bob.post("/api/auth/login", json={"username": "carol", "password": "password123"})
        assert (await bob.get("/api/agents")).status_code == 200
        await admin.patch(f"/api/users/{carol['id']}", json={"enabled": False})
        assert (await bob.get("/api/agents")).status_code == 401
        assert (await bob.post("/api/auth/login", json={"username": "carol", "password": "password123"})).status_code == 403
        # 重設密碼
        await admin.patch(f"/api/users/{carol['id']}", json={"enabled": True, "password": "resetpass1"})
        assert (await bob.post("/api/auth/login", json={"username": "carol", "password": "resetpass1"})).status_code == 200
        assert (await admin.delete(f"/api/users/{carol['id']}")).status_code == 204


async def test_runs_are_private(app):
    a, _ = app
    async with client(a) as admin, client(a) as bob, client(a) as eve:
        await admin.post("/api/auth/register", json={"username": "admin", "password": "password123"})
        await bob.post("/api/auth/register", json={"username": "bob", "password": "password123"})
        await eve.post("/api/auth/register", json={"username": "eve", "password": "password123"})
        from test_villagers import _register_model

        await _register_model(bob)
        run_id = (await bob.post("/api/chat", json={"message": "bob 的秘密問題"})).json()["run_id"]
        assert [r["id"] for r in (await bob.get("/api/runs")).json()] == [run_id]
        assert (await eve.get("/api/runs")).json() == []
        assert (await eve.get(f"/api/runs/{run_id}")).status_code == 404
        await eve.delete(f"/api/runs/{run_id}")  # 刪不到別人的
        listing = (await admin.get("/api/runs")).json()  # 管理員看得到全部，並標出提問者
        assert listing[0]["id"] == run_id and listing[0]["user"] == "bob"


def test_events_hide_other_users_content():
    e = {"type": "agent.finished", "ts": "t", "run_id": "r", "user_id": "bob", "agent": "poet", "output": "秘密"}
    assert for_viewer(e, "bob") == e
    assert for_viewer(e, "eve") == {"type": "agent.finished", "ts": "t", "agent": "poet"}  # 只剩動畫需要的
    assert for_viewer({"type": "agents.changed", "agents": ["poet"]}, "eve")["agents"] == ["poet"]  # 公開事件照送


def test_password_hash_roundtrip():
    h = auth.hash_password("correct horse")
    assert h.startswith("scrypt$") and auth.verify_password("correct horse", h)
    assert not auth.verify_password("wrong", h) and not auth.verify_password("x", "garbage")
