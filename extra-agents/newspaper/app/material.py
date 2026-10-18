"""採訪資料：收到的 txt / md（多半是網路研究員查好的成果）→ 每份一個「資料」，資料來源重新編號。

網路研究員的成果長這樣：本文裡用 [1]、[2] 標註，最後一段是
    資料來源：
    [1] 《標題》段落　https://…
好幾份資料的 [1] 會撞號，所以每份資料用一個字母當前綴：第一份的 [1] 變成 [A1]、第二份的變成 [B1]，交給 LLM 引用；
排版時再換成整份報紙統一的編號。資料內容一律當資料，不當指令。
"""

import re
from dataclasses import dataclass, field
from pathlib import PurePath

from app.config import Limits

SUPPORTED = {".txt": "文字檔", ".md": "Markdown"}
_ENCODINGS = ["utf-8-sig", "cp950", "big5hkscs", "gb18030"]
_SOURCES_HEAD = re.compile(r"^\s*(?:#+\s*)?(資料來源|參考資料|來源|Sources?|References?)\s*[:：]?\s*$", re.I | re.M)
_SOURCE_LINE = re.compile(r"^\s*\[(\d{1,3})\]\s*(.+?)\s*$")
_URL = re.compile(r"https?://\S+")
_CITE = re.compile(r"\[(\d{1,3})\]")
_TITLE = re.compile(r"^\s*#{1,3}\s+(.+?)\s*$", re.M)


class ReadError(ValueError):
    """訊息直接給使用者看。"""


@dataclass
class Source:
    id: str  # A1、B3…（交給 LLM 引用的編號）
    title: str
    url: str


@dataclass
class Material:
    label: str  # A、B、C…
    name: str  # 檔名
    text: str  # 本文（引用已改成 [A1]）
    sources: list[Source] = field(default_factory=list)
    truncated: bool = False

    @property
    def title(self) -> str:
        """本文第一個標題，沒有就用檔名。"""
        m = _TITLE.search(self.text)
        return m.group(1).strip("*# ") if m else PurePath(self.name).stem


def _decode(data: bytes) -> str:
    for enc in _ENCODINGS:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def check_batch(files: list[tuple[str, bytes]], limits: Limits) -> None:
    if not files:
        raise ReadError("沒有收到採訪資料：請附上 txt 或 md 檔（例如網路研究員查好的新聞）")
    if len(files) > limits.max_files:
        raise ReadError(f"一次最多 {limits.max_files} 份資料")
    if sum(len(d) for _, d in files) > limits.max_total_bytes:
        raise ReadError(f"資料合計超過 {limits.max_total_bytes // 1024 // 1024} MB")
    for name, data in files:
        ext = PurePath(name).suffix.lower()
        if ext not in SUPPORTED:
            raise ReadError(f"「{name}」的格式不支援（可以附 {'、'.join(SUPPORTED)}）")
        if len(data) > limits.max_file_bytes:
            raise ReadError(f"「{name}」超過每份 {limits.max_file_bytes // 1024 // 1024} MB 的上限")
        if b"\x00" in data[:4096]:
            raise ReadError(f"「{name}」看起來不是文字檔")


def split_sources(text: str) -> tuple[str, list[tuple[str, str, str]]]:
    """把最後的「資料來源」段落拆出來：回傳（本文, [(原編號, 標題, 網址)]）。"""
    heads = list(_SOURCES_HEAD.finditer(text))
    if not heads:
        return text, []
    head = heads[-1]
    body, tail = text[: head.start()], text[head.end():]
    found = []
    for line in tail.splitlines():
        m = _SOURCE_LINE.match(line)
        if not m:
            continue
        rest = m.group(2)
        url_m = _URL.search(rest)
        url = url_m.group(0).rstrip("）)。，,") if url_m else ""
        title = _URL.sub("", rest).strip(" 　-—|·・").strip("《》 ") or url
        title = re.sub(r"》\s*", "》", title)
        found.append((m.group(1), title.replace("《", "").replace("》", " ").strip(), url))
    return (body.rstrip() if found else text), found


def read(files: list[tuple[str, bytes]], limits: Limits, doc_chars: int) -> list[Material]:
    check_batch(files, limits)
    out: list[Material] = []
    for i, (name, data) in enumerate(files):
        label = chr(ord("A") + i)
        text = _decode(data).replace("\r\n", "\n").strip()
        body, found = split_sources(text)
        known = {n for n, _, _ in found}
        # 對得上來源的改成 [A1]；對不上的（資料自己標錯）拿掉，免得排出沒有出處的編號
        body = _CITE.sub(lambda m: f"[{label}{m.group(1)}]" if m.group(1) in known else "", body)
        truncated = len(body) > doc_chars
        if truncated:
            body = body[:doc_chars] + "…（後略）"
        if not body.strip():
            raise ReadError(f"「{name}」是空的")
        out.append(Material(label, name, body.strip(), [Source(f"{label}{n}", t, u) for n, t, u in found], truncated))
    return out


def render_for_llm(materials: list[Material]) -> str:
    blocks = []
    for m in materials:
        src = "\n".join(f"[{s.id}] {s.title}　{s.url}".rstrip() for s in m.sources) or "（這份資料沒有附來源）"
        blocks.append(f"=== 資料 {m.label}：{m.name} ===\n{m.text}\n\n資料 {m.label} 的來源：\n{src}")
    return "\n\n".join(blocks)
