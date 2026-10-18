"""搜尋：呼叫自架的 SearXNG（JSON API）。SearXNG 再去問 Google、Bing、DuckDuckGo、維基百科等，不需要任何 API key。"""

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


class SearchError(RuntimeError):
    pass


@dataclass
class Hit:
    title: str
    url: str
    snippet: str
    published: str | None = None
    engine: str | None = None

    @property
    def site(self) -> str:
        host = urlparse(self.url).hostname or ""
        return host.removeprefix("www.")


def _client(**kw) -> httpx.AsyncClient:  # 測試會替換這個
    return httpx.AsyncClient(**kw)


async def search(base_url: str, query: str, language: str = "zh-TW", limit: int = 10) -> list[Hit]:
    try:
        async with _client(timeout=15.0) as http:
            r = await http.get(base_url.rstrip("/") + "/search",
                               params={"q": query, "format": "json", "language": language, "safesearch": 1})
    except httpx.HTTPError as e:
        raise SearchError(f"連不上搜尋引擎（SearXNG）：{type(e).__name__}") from None
    if r.status_code == 403:
        raise SearchError("搜尋引擎拒絕 JSON 查詢：SearXNG 的 settings.yml 要在 search.formats 加上 json")
    if r.status_code != 200:
        raise SearchError(f"搜尋引擎回傳 HTTP {r.status_code}")
    try:
        results = r.json().get("results") or []
    except ValueError:
        raise SearchError("搜尋引擎的回覆不是 JSON") from None
    hits: list[Hit] = []
    for x in results:
        url = str(x.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        hits.append(Hit(
            title=str(x.get("title") or url).strip()[:200],
            url=url,
            snippet=str(x.get("content") or "").strip()[:500],
            published=str(x["publishedDate"])[:10] if x.get("publishedDate") else None,
            engine=x.get("engine"),
        ))
        if len(hits) >= limit:
            break
    return hits


def merge(lists: list[list[Hit]], max_pages: int, per_site: int) -> list[Hit]:
    """幾組關鍵字的結果輪流取（第一名們、第二名們…），去掉重複網址；同一個網站最多 per_site 頁。"""
    seen: set[str] = set()
    sites: dict[str, int] = {}
    out: list[Hit] = []
    for rank in range(max((len(x) for x in lists), default=0)):
        for hits in lists:
            if rank >= len(hits):
                continue
            h = hits[rank]
            key = h.url.split("#")[0].rstrip("/")
            if key in seen or sites.get(h.site, 0) >= per_site:
                continue
            seen.add(key)
            sites[h.site] = sites.get(h.site, 0) + 1
            out.append(h)
            if len(out) >= max_pages:
                return out
    return out
