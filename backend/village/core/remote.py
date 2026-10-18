"""駐村專家（外接的獨立 agent）：讀取 agent card、健康檢查、把 card 轉成村民定義。

駐村專家是在別處獨立開發、獨立部署的 agent（例如 extra-agents/taiwan-history），村子用 A2A 協定呼叫：
  - GET  {網址}/.well-known/agent-card.json   自我介紹（名稱、說明、技能、A2A 的路徑）
  - GET  {網址}/health                         健康檢查（沒有這個路徑就改讀 agent card）
  - POST {網址}{a2a_path}                       問答（A2A message/stream，不支援就改用 message/send）

收文件的駐村專家（名片寫著收哪些檔案，例如文件彙整師）：問答附的文件放在訊息的 file part（base64）一起送；
對方回傳的檔案（例如報告 PDF）存進 chat_files，回答附上下載連結。

對方 card 裡寫的網址通常是它自己看到的（例如 localhost），從村子的容器連不到；
所以一律用「管理員填的網址」加上 card 裡的路徑去呼叫。
"""

import base64
import binascii
import json
import re
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from sqlalchemy import select

from village.core.card import AgentSpec
from village.core.docfiles import MAX_FILES, MAX_TOTAL_BYTES, DocFile, accepts_from_card, for_agent, safe_name
from village.llm.base import LLMError, TextCallback
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
        "remote": {"url": base, "a2a_path": a2a_path(card), "share_llm": share_llm, "accepts": accepts_from_card(card)},
    })


# ---------------------------------------------------------------- 問答時呼叫（A2A message/send）

CALL_TIMEOUT = 900.0  # 駐村專家可能自己還要檢索、呼叫 LLM、產生報告；實際上限由村規的「每一步時間上限」決定
# 對方回傳的檔案只收這些類型（下載時一律當附件，不在瀏覽器裡直接開）；其他類型存成 octet-stream
RETURN_MIMES = {
    "application/pdf", "text/plain", "text/csv", "image/png", "image/jpeg",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


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
    docs: list[DocFile] = field(default_factory=list)  # 這次問答附的文件（只交給收這類檔案的駐村專家）
    # 存對方回傳的檔案（name, mime, data）→ 檔案 id；沒有就不收檔案
    save_file: Callable[[str, str, bytes], Awaitable[str]] | None = None


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


@dataclass
class Reply:
    text: str
    data: dict = field(default_factory=dict)
    files: list[DocFile] = field(default_factory=list)  # 對方回傳的檔案（例如報告 PDF）


def _file_part(part: dict) -> DocFile | None:
    f = part.get("file") if isinstance(part.get("file"), dict) else {}
    if "bytes" not in f:
        return None  # 不支援 uri（不去別處下載檔案）
    try:
        data = base64.b64decode(f["bytes"], validate=True)
    except (binascii.Error, TypeError, ValueError):
        raise LLMError("駐村專家回傳的檔案格式不對（base64）") from None
    if not data or len(data) > MAX_TOTAL_BYTES:
        raise LLMError("駐村專家回傳的檔案是空的或太大")
    mime = str(f.get("mimeType") or "")
    return DocFile(safe_name(str(f.get("name") or "檔案")), mime if mime in RETURN_MIMES else "application/octet-stream", data)


def _collect(parts: list, reply: Reply) -> None:
    for p in parts:
        if not isinstance(p, dict):
            continue
        if p.get("kind") == "data" and isinstance(p.get("data"), dict):
            reply.data.update(p["data"])
        elif p.get("kind") == "file" and (f := _file_part(p)):
            reply.files.append(f)


def parse_reply(body: Any) -> Reply:
    """JSON-RPC 回覆 → Reply（文字、data part、檔案）。支援直接回 Message，或回 Task（取 artifacts / status.message）。"""
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
    reply = Reply(text)
    _collect(parts, reply)
    if not text and not reply.files:
        raise LLMError("駐村專家沒有回答內容")
    return reply


_NO_STREAM: set[str] = set()  # 回過「不支援 message/stream」的 A2A 網址：之後直接用 message/send


async def _headers(env: RemoteEnv, spec, warnings: list[str]) -> dict:
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
    return headers


def _rpc(method: str, instruction: str, context: str, files: list[DocFile] | None = None) -> dict:
    parts: list[dict] = [{"kind": "text", "text": render_message(instruction, context)}]
    parts += [
        {"kind": "file", "file": {"name": f.name, "mimeType": f.mime, "bytes": base64.b64encode(f.data).decode()}}
        for f in files or []
    ]
    message = {"kind": "message", "role": "user", "messageId": uuid.uuid4().hex, "parts": parts}
    return {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": {"message": message}}


def _check_status(status: int, detail: str) -> None:
    if status == 401:
        raise LLMError("駐村專家拒絕了存取 token（到村民頁 → 管理，確認 token）")
    if status >= 400:
        raise LLMError(f"駐村專家回傳 HTTP {status}：{detail[:300]}")


async def _send(http: httpx.AsyncClient, url: str, rpc: dict, headers: dict) -> Reply:
    r = await http.post(url, json=rpc, headers=headers)
    _check_status(r.status_code, r.text)
    try:
        body = r.json()
    except ValueError:
        raise LLMError("駐村專家的回覆不是 JSON") from None
    return parse_reply(body)


class _NotStreaming(Exception):
    """對方不支援 message/stream（回 -32601）：改用 message/send。"""


async def _stream(http: httpx.AsyncClient, url: str, rpc: dict, headers: dict, on_text: TextCallback) -> Reply:
    """A2A message/stream（SSE）：文字一段一段累積，每次把「目前為止的完整文字」交給 on_text。

    還沒有回答文字時，對方的進度（status-update 帶的訊息，例如「讀取檔案 2/3」）也用 on_text 顯示。
    """
    reply = Reply("")
    async with http.stream("POST", url, json=rpc, headers={**headers, "Accept": "text/event-stream"}) as r:
        if r.status_code >= 400:
            _check_status(r.status_code, (await r.aread()).decode(errors="replace"))
        if not r.headers.get("content-type", "").startswith("text/event-stream"):
            try:
                body = json.loads(await r.aread())
            except ValueError:
                raise LLMError("駐村專家的回覆不是 JSON") from None
            if isinstance(body, dict) and (body.get("error") or {}).get("code") == -32601:
                raise _NotStreaming
            return parse_reply(body)  # 對方沒有串流、直接回完整結果
        async for line in r.aiter_lines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:])
            except json.JSONDecodeError:
                continue
            if err := event.get("error"):
                raise LLMError(f"駐村專家回報錯誤：{err.get('message') if isinstance(err, dict) else err}")
            result = event.get("result") or {}
            kind = result.get("kind")
            if kind == "artifact-update":
                parts = (result.get("artifact") or {}).get("parts") or []
                for part in parts:
                    if part.get("kind") == "text":
                        reply.text = reply.text + part.get("text", "") if result.get("append") else part.get("text", "")
                        await on_text(reply.text)
                _collect(parts, reply)
            elif kind == "message":  # 有些 agent 最後直接送完整的 Message
                more = parse_reply({"result": result})
                reply.text, reply.files = more.text, reply.files + more.files
                reply.data.update(more.data)
                await on_text(reply.text)
            elif kind == "status-update":
                state = (result.get("status") or {}).get("state")
                if state in ("failed", "rejected", "canceled"):
                    raise LLMError(f"駐村專家的任務沒有完成（{state}）")
                if result.get("final"):
                    break
                note = " ".join(p.get("text", "") for p in ((result.get("status") or {}).get("message") or {}).get("parts") or []
                                if isinstance(p, dict) and p.get("kind") == "text").strip()
                if note and not reply.text:
                    await on_text(f"⏳ {note}…")
    reply.text = reply.text.strip()
    if not reply.text and not reply.files:
        raise LLMError("駐村專家沒有回答內容")
    return reply


# 這一步從中轉站取來的檔案（前面步驟的成果；executor 在呼叫前設定）
_HANDED: ContextVar[list[DocFile]] = ContextVar("handed_files", default=[])


@contextmanager
def handing(files: list[DocFile]) -> Iterator[None]:
    token = _HANDED.set(files)
    try:
        yield
    finally:
        _HANDED.reset(token)


def files_for(spec) -> list[DocFile]:
    """這位駐村專家這次會拿到的文件（名片上寫著收的類型才給）：使用者附的優先，再來是中轉站取來的，最多 MAX_FILES 份。"""
    env = REMOTE_ENV.get()
    if env is None or spec.remote is None or not spec.remote.accepts:
        return []
    return for_agent(env.docs + _HANDED.get(), spec.remote.accepts)[:MAX_FILES]


async def call(spec, instruction: str, context: str, on_text: TextCallback | None = None) -> tuple[Reply, list[str]]:
    """呼叫駐村專家；回傳 (回覆, 警告)。

    有 on_text 時先試 message/stream（邊收邊顯示）；對方不支援就改用 message/send，並記住這個網址。
    """
    env = REMOTE_ENV.get()
    if env is None or spec.remote is None:
        raise LLMError("駐村專家只能在問答或村民試打裡呼叫")
    warnings: list[str] = []
    files = files_for(spec)
    if spec.remote.accepts and not files:
        kinds = "、".join(spec.remote.accepts)
        raise LLMError(f"「{spec.name}」需要附上文件（{kinds}）：在問答視窗按 📎 附上文件再問一次")
    headers = await _headers(env, spec, warnings)
    url = spec.remote.url.rstrip("/") + spec.remote.a2a_path
    try:
        async with _client(CALL_TIMEOUT) as http:
            result = None
            if on_text and url not in _NO_STREAM:
                try:
                    result = await _stream(http, url, _rpc("message/stream", instruction, context, files), headers, on_text)
                except _NotStreaming:
                    _NO_STREAM.add(url)
            if result is None:
                result = await _send(http, url, _rpc("message/send", instruction, context, files), headers)
    except httpx.HTTPError as e:
        raise LLMError(f"連不上駐村專家（{url}）：{type(e).__name__}") from None
    if result.data.get("llm_error"):
        what = "只產生統計版報告" if files else "只列出找到的資料"
        warnings.append(f"駐村專家沒有用 LLM 整理（{result.data['llm_error']}），{what}")
        if not spec.remote.share_llm:
            warnings.append("可以到村民頁 → 管理，打開「允許把提問者的 LLM 設定交給它」")
    return result, warnings
