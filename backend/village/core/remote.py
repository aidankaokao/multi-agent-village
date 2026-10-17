"""駐村專家（外接的獨立 agent）：讀取 agent card、健康檢查、把 card 轉成村民定義。

駐村專家是在別處獨立開發、獨立部署的 agent（例如 extra-agents/taiwan-history），村子用 A2A 協定呼叫：
  - GET  {網址}/.well-known/agent-card.json   自我介紹（名稱、說明、技能、A2A 的路徑）
  - GET  {網址}/health                         健康檢查（沒有這個路徑就改讀 agent card）
  - POST {網址}{a2a_path}                       問答（下一步 B 才接上）

對方 card 裡寫的網址通常是它自己看到的（例如 localhost），從村子的容器連不到；
所以一律用「管理員填的網址」加上 card 裡的路徑去呼叫。
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from sqlalchemy import select

from village.core.card import AgentSpec
from village.llm.base import LLMError
from village.security.redact import register_secret

CARD_PATH = "/.well-known/agent-card.json"
TIMEOUT = 10.0


class RemoteError(RuntimeError):
    pass


def split_url(url: str) -> tuple[str, str]:
    """管理員可以填服務網址或直接貼 agent card 網址；回傳 (服務網址, card 網址)。"""
    url = url.strip()
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise RemoteError("網址要以 http:// 或 https:// 開頭，例如 http://host.docker.internal:8100")
    if u.path.endswith(CARD_PATH):
        base = f"{u.scheme}://{u.netloc}{u.path[: -len(CARD_PATH)]}".rstrip("/")
        return base, url
    base = url.rstrip("/")
    return base, base + CARD_PATH


def _client(timeout: float = TIMEOUT) -> httpx.AsyncClient:  # 測試會替換這個
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


async def fetch_card(url: str) -> tuple[str, dict[str, Any]]:
    """回傳 (服務網址, agent card)。"""
    base, card_url = split_url(url)
    try:
        async with _client() as http:
            r = await http.get(card_url)
    except httpx.HTTPError as e:
        raise RemoteError(f"連不上 {card_url}：{type(e).__name__}（容器裡的村子要用 host.docker.internal 連主機上的服務）") from None
    if r.status_code != 200:
        raise RemoteError(f"讀不到 agent card（{card_url} 回 HTTP {r.status_code}）")
    try:
        card = r.json()
    except ValueError:
        raise RemoteError("agent card 不是 JSON") from None
    if not isinstance(card, dict) or not card.get("name") or not card.get("description"):
        raise RemoteError("agent card 缺少 name 或 description，看起來不是 A2A 的 agent card")
    return base, card


def a2a_path(card: dict) -> str:
    """card 裡的 url（A2A 端點）只取路徑，主機用管理員填的網址。"""
    path = urlparse(str(card.get("url") or "")).path
    return path if path and path != "/" else "/a2a"


async def check_health(base: str) -> tuple[bool, str | None]:
    """回傳 (在線, 錯誤說明)。先看 /health，沒有這個路徑就改讀 agent card。"""
    try:
        async with _client() as http:
            r = await http.get(base.rstrip("/") + "/health")
            if r.status_code == 404:
                r = await http.get(base.rstrip("/") + CARD_PATH)
    except httpx.HTTPError as e:
        return False, f"連不上：{type(e).__name__}"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    try:
        data = r.json()
    except ValueError:
        return True, None
    if isinstance(data, dict) and data.get("ok") is False:  # 例如台灣史專家的索引還沒建好
        return False, "服務還沒準備好（例如索引還在建立）"
    return True, None


def suggest_id(card: dict, base: str) -> str:
    """建議的村民 id（英文小寫）：card 名稱是英文就用它，否則用第一個技能的 id 或網址的主機名。"""
    candidates = [str(card.get("name") or "")]
    candidates += [str(s.get("id") or "") for s in card.get("skills") or [] if isinstance(s, dict)]
    candidates.append(urlparse(base).hostname or "")
    for raw in candidates:
        slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
        slug = re.sub(r"-(qa|agent|skill)$", "", slug)
        if slug and slug[0].isalpha() and slug not in ("localhost", "host-docker-internal"):
            return slug[:40]
    return "resident"


def spec_from_card(
    card: dict, agent_id: str, base: str, *, name: str | None = None, sprite: str | None = None,
    home: tuple[int, int] | None = None, keywords: list[str] | None = None, share_llm: bool = False,
) -> AgentSpec:
    skills = []
    for i, s in enumerate(card.get("skills") or []):
        if not isinstance(s, dict):
            continue
        sid = re.sub(r"[^a-z0-9_-]+", "_", str(s.get("id") or f"skill{i + 1}").lower()) or f"skill{i + 1}"
        desc = str(s.get("description") or s.get("name") or sid)
        skills.append({"id": sid, "description": desc, "examples": [str(x) for x in (s.get("examples") or [])][:5]})
    tags = sorted({str(t) for s in card.get("skills") or [] if isinstance(s, dict) for t in s.get("tags") or []})[:8]
    return AgentSpec.model_validate({
        "card": {
            "id": agent_id,
            "name": (name or str(card["name"])).strip()[:40],
            "description": str(card["description"]).strip(),
            "version": str(card.get("version") or "0.1.0"),
            "skills": skills,
            "tags": tags,
        },
        "x-village": {"sprite": sprite or "scholar", "home": home, "keywords": keywords or []},
        "kind": "remote",
        "remote": {"url": base, "a2a_path": a2a_path(card), "share_llm": share_llm},
    })


# ---------------------------------------------------------------- 問答時呼叫（A2A message/send）

CALL_TIMEOUT = 300.0  # 駐村專家可能自己還要檢索、呼叫 LLM；實際上限由村規的「每一步時間上限」決定


class RemoteDirectory:
    """從 DB 讀駐村專家的狀態與（解密後的）存取 token。"""

    def __init__(self, sessionmaker, box):
        self._sm = sessionmaker
        self._box = box

    async def online_ids(self) -> set[str]:
        from village.store.models import RemoteAgent

        async with self._sm() as s:
            return set((await s.scalars(select(RemoteAgent.agent_id).where(RemoteAgent.status == "online"))).all())

    async def token(self, agent_id: str) -> str | None:
        from village.store.models import RemoteAgent

        async with self._sm() as s:
            ra = await s.get(RemoteAgent, agent_id)
        if ra is None or ra.token_ct is None:
            return None
        token = self._box.decrypt(ra.token_ct, ra.token_nonce, ra.key_version, ra.token_aad)
        register_secret(token)  # 之後若出現在 log / 錯誤訊息會被遮罩
        return token


@dataclass
class RemoteEnv:
    """這次問答呼叫駐村專家需要的東西（Runner 是全村共用的，所以放 context variable）。"""

    directory: RemoteDirectory
    llm: Any  # LLMService（取提問者可以分享的 LLM 設定）
    user_id: str | None
    shared_llm: dict | None = field(default=None)  # 快取：同一次問答只查一次


REMOTE_ENV: ContextVar[RemoteEnv | None] = ContextVar("remote_env", default=None)


@contextmanager
def remote_calls(env: RemoteEnv | None) -> Iterator[None]:
    token = REMOTE_ENV.set(env)
    try:
        yield
    finally:
        REMOTE_ENV.reset(token)


def render_message(instruction: str, context: str) -> str:
    return f"{instruction}\n\n{context}".strip() if context.strip() else instruction


def render_citations(data: dict) -> str:
    """把回覆 data part 裡的引用整理成「資料來源」，附在成果後面（[編號] 對得上回答裡的標註）。"""
    lines = []
    for c in data.get("citations") or []:
        if not isinstance(c, dict):
            continue
        n, title, section, url = c.get("n"), c.get("title"), c.get("section") or "", unquote(c.get("url") or "")
        section = section.removeprefix(str(title)).removeprefix(" › ") if title else section
        label = f"《{title}》{section}" if title else section or url
        lines.append(f"[{n}] {label}　{url}".rstrip() if n is not None else f"・{label}　{url}".rstrip())
    return "資料來源：\n" + "\n".join(lines) if lines else ""


def parse_reply(body: Any) -> tuple[str, dict]:
    """JSON-RPC 回覆 → (文字, data part)。支援直接回 Message，或回 Task（取 artifacts / status.message）。"""
    if not isinstance(body, dict):
        raise LLMError("駐村專家的回覆不是 JSON 物件")
    if err := body.get("error"):
        msg = err.get("message") if isinstance(err, dict) else err
        raise LLMError(f"駐村專家回報錯誤：{msg}")
    result = body.get("result")
    if not isinstance(result, dict):
        raise LLMError("駐村專家的回覆沒有 result")
    parts: list = []
    if result.get("kind") == "task":
        state = (result.get("status") or {}).get("state")
        if state in ("failed", "rejected", "canceled"):
            raise LLMError(f"駐村專家的任務沒有完成（{state}）")
        for a in result.get("artifacts") or []:
            parts += a.get("parts") or []
        if not parts:
            parts = ((result.get("status") or {}).get("message") or {}).get("parts") or []
    else:
        parts = result.get("parts") or []
    text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("kind") == "text").strip()
    data: dict = {}
    for p in parts:
        if isinstance(p, dict) and p.get("kind") == "data" and isinstance(p.get("data"), dict):
            data.update(p["data"])
    if not text:
        raise LLMError("駐村專家沒有回答內容")
    return text, data


async def call(spec, instruction: str, context: str) -> tuple[str, dict, list[str]]:
    """呼叫駐村專家；回傳 (成果文字, data part, 警告)。"""
    env = REMOTE_ENV.get()
    if env is None or spec.remote is None:
        raise LLMError("駐村專家只能在問答或村民試打裡呼叫")
    warnings: list[str] = []
    headers = {"Content-Type": "application/json"}
    if token := await env.directory.token(spec.id):
        headers["Authorization"] = f"Bearer {token}"
    if spec.remote.share_llm:
        if env.shared_llm is None:
            env.shared_llm = await env.llm.shareable(env.user_id) or {}
        cfg = env.shared_llm
        if cfg:
            headers["X-LLM-Base-URL"], headers["X-LLM-Model"] = cfg["base_url"], cfg["model"]
            if cfg.get("api_key"):
                headers["X-LLM-API-Key"] = cfg["api_key"]
            if cfg.get("reasoning_effort"):
                headers["X-LLM-Reasoning-Effort"] = cfg["reasoning_effort"]
        else:
            warnings.append("你的 large 分級沒有 OpenAI 相容的模型（Anthropic 不行），沒有提供 LLM 給駐村專家")
    message = {
        "kind": "message", "role": "user", "messageId": uuid.uuid4().hex,
        "parts": [{"kind": "text", "text": render_message(instruction, context)}],
    }
    rpc = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "message/send", "params": {"message": message}}
    url = spec.remote.url.rstrip("/") + spec.remote.a2a_path
    try:
        async with _client(CALL_TIMEOUT) as http:
            r = await http.post(url, json=rpc, headers=headers)
    except httpx.HTTPError as e:
        raise LLMError(f"連不上駐村專家（{url}）：{type(e).__name__}") from None
    if r.status_code == 401:
        raise LLMError("駐村專家拒絕了存取 token（到村民頁 → 管理，確認 token）")
    if r.status_code >= 400:
        raise LLMError(f"駐村專家回傳 HTTP {r.status_code}：{r.text[:300]}")
    try:
        body = r.json()
    except ValueError:
        raise LLMError("駐村專家的回覆不是 JSON") from None
    text, data = parse_reply(body)
    if data.get("llm_error"):
        warnings.append(f"駐村專家沒有用 LLM 整理回答（{data['llm_error']}），只列出找到的資料")
        if not spec.remote.share_llm:
            warnings.append("可以到村民頁 → 管理，打開「允許把提問者的 LLM 設定交給它」")
    return text, data, warnings
