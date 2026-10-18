"""抓網頁：只抓公開網址（擋內網、本機位址，避免被拿去探測內網）、限制大小與時間，再取出正文。

網頁內容一律當作資料，不當作指令（回答的提示詞會再講一次）。
"""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; VillageWebResearcher/0.1; +https://github.com/aidankaokao/multi-agent-village)"
MAX_REDIRECTS = 3


class FetchError(RuntimeError):
    pass


@dataclass
class Page:
    url: str
    title: str
    text: str
    date: str | None = None


def _client(**kw) -> httpx.AsyncClient:  # 測試會替換這個
    return httpx.AsyncClient(**kw)


async def _resolve(host: str) -> list[str]:  # 測試會替換這個
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [i[4][0] for i in infos]


async def check_public(url: str) -> None:
    """只允許 http(s)、而且主機解析出來的每個位址都是公開位址。"""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FetchError("不是 http(s) 網址")
    try:
        addrs = await _resolve(u.hostname)
    except (OSError, UnicodeError):
        raise FetchError(f"找不到主機 {u.hostname}") from None
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise FetchError(f"{u.hostname} 是內網或保留位址，不抓")


async def fetch(url: str, max_bytes: int, timeout: float) -> tuple[str, bytes, str]:
    """回傳 (最後的網址, 內容, content-type)。轉址自己處理，每一跳都檢查位址。"""
    async with _client(timeout=timeout, follow_redirects=False, headers={"User-Agent": USER_AGENT,
                                                                          "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.6"}) as http:
        for _ in range(MAX_REDIRECTS + 1):
            await check_public(url)
            try:
                async with http.stream("GET", url) as r:
                    if r.is_redirect and (loc := r.headers.get("location")):
                        url = urljoin(url, loc)
                        continue
                    if r.status_code >= 400:
                        raise FetchError(f"HTTP {r.status_code}")
                    ctype = r.headers.get("content-type", "").lower()
                    if not any(t in ctype for t in ("text/html", "application/xhtml", "text/plain")):
                        raise FetchError(f"不是網頁（{ctype.split(';')[0] or '未知類型'}）")
                    body = bytearray()
                    async for chunk in r.aiter_bytes():
                        body += chunk
                        if len(body) > max_bytes:
                            break  # 太大的網頁只取前面
                    return url, bytes(body[:max_bytes]), ctype
            except httpx.HTTPError as e:
                raise FetchError(f"連不上：{type(e).__name__}") from None
    raise FetchError("轉址太多次")


def extract(url: str, body: bytes, ctype: str) -> Page:
    """取正文（去掉選單、廣告、頁尾）。在執行緒裡跑（trafilatura 是同步的）。"""
    import trafilatura

    if "text/plain" in ctype:
        text = body.decode("utf-8", errors="replace")
        return Page(url, urlparse(url).path.rsplit("/", 1)[-1] or url, text.strip())
    html = body.decode("utf-8", errors="replace")
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_precision=True) or ""
    meta = trafilatura.extract_metadata(html, default_url=url)
    title = (meta.title if meta and meta.title else "") or url
    date = meta.date if meta and meta.date else None
    return Page(url, title.strip()[:200], text.strip(), date)


async def read(url: str, max_bytes: int, timeout: float) -> Page:
    final, body, ctype = await fetch(url, max_bytes, timeout)
    page = await asyncio.to_thread(extract, final, body, ctype)
    if len(page.text) < 80:
        raise FetchError("網頁沒有可讀的正文（可能要執行 JavaScript 才看得到）")
    return page
