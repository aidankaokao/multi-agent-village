"""駐村專家（外接的獨立 agent）：讀取 agent card、健康檢查、把 card 轉成村民定義。

駐村專家是在別處獨立開發、獨立部署的 agent（例如 extra-agents/taiwan-history），村子用 A2A 協定呼叫：
  - GET  {網址}/.well-known/agent-card.json   自我介紹（名稱、說明、技能、A2A 的路徑）
  - GET  {網址}/health                         健康檢查（沒有這個路徑就改讀 agent card）
  - POST {網址}{a2a_path}                       問答（下一步 B 才接上）

對方 card 裡寫的網址通常是它自己看到的（例如 localhost），從村子的容器連不到；
所以一律用「管理員填的網址」加上 card 裡的路徑去呼叫。
"""

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from village.core.card import AgentSpec

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


def _client() -> httpx.AsyncClient:  # 測試會替換這個
    return httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)


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
