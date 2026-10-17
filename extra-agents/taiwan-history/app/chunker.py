"""把 corpus 的 Markdown 文件切成檢索用的段落（chunk）。

- 依章節切：每塊都帶「條目 › 章節 › 小節」路徑，檢索與引用時知道出處
- 章節太長就依段落累積到約 TARGET 字再切；單一段落過長才硬切
- 太短的章節（只有一兩句）併到下一塊，避免一堆零碎小塊
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

TARGET = 600  # 每塊的目標字數
MAX = 900  # 單一段落超過這個長度才硬切
MIN = 120  # 比這個短的章節併到下一塊

_HEADING = re.compile(r"^(#{1,6}) (.+)$")


@dataclass
class Doc:
    title: str
    url: str
    period: str
    period_name: str
    revision: int | None
    body: str
    path: Path

    @property
    def digest(self) -> str:
        """內容指紋：文件沒變就不用重新向量化。"""
        return hashlib.sha256(self.body.encode()).hexdigest()[:16]


@dataclass
class Chunk:
    doc_title: str
    url: str
    period: str
    period_name: str
    section: str  # 「條目 › 章節 › 小節」
    text: str
    ordinal: int  # 在文件裡的順序

    def embed_text(self) -> str:
        """拿去向量化的文字：帶上出處路徑，「事件經過」這種章節才知道是哪個事件。"""
        return f"{self.section}\n{self.text}"


def load_doc(path: Path) -> Doc:
    raw = path.read_text(encoding="utf-8")
    _, front, body = raw.split("---", 2)
    meta = yaml.safe_load(front)
    return Doc(
        title=meta["title"], url=meta["url"], period=meta["period"], period_name=meta["period_name"],
        revision=meta.get("revision"), body=body.strip(), path=path,
    )


def load_corpus(corpus_dir: Path) -> list[Doc]:
    return [load_doc(p) for p in sorted(corpus_dir.rglob("*.md"))]


def _sections(doc: Doc) -> list[tuple[str, list[str]]]:
    """[(章節路徑, [段落…])]"""
    path: list[str] = []
    out: list[tuple[str, list[str]]] = []
    for block in doc.body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if m := _HEADING.match(block):
            level = len(m.group(1))
            path = path[: level - 1] + [m.group(2)]
            out.append((" › ".join(path), []))
        else:
            if not out:
                out.append((doc.title, []))
            out[-1][1].append(block)
    return [(s, ps) for s, ps in out if ps]


def _split_long(paragraph: str) -> list[str]:
    """過長的段落依句號切開再累積。"""
    if len(paragraph) <= MAX:
        return [paragraph]
    sentences = re.split(r"(?<=[。！？；])", paragraph)
    pieces, buf = [], ""
    for s in sentences:
        if buf and len(buf) + len(s) > TARGET:
            pieces.append(buf)
            buf = ""
        buf += s
    if buf:
        pieces.append(buf)
    return pieces


def chunk_doc(doc: Doc) -> list[Chunk]:
    chunks: list[Chunk] = []
    carry: tuple[str, str] | None = None  # 太短、要併到下一塊的章節

    def emit(section: str, text: str) -> None:
        chunks.append(Chunk(doc.title, doc.url, doc.period, doc.period_name, section, text, len(chunks)))

    for section, paragraphs in _sections(doc):
        pieces = [p for para in paragraphs for p in _split_long(para)]
        buf = ""
        if carry:
            buf = f"【{carry[0]}】{carry[1]}\n\n"
            carry = None
        for piece in pieces:
            if buf and len(buf) + len(piece) > TARGET:
                emit(section, buf.strip())
                buf = ""
            buf += piece + "\n\n"
        buf = buf.strip()
        if buf:
            if len(buf) < MIN:
                carry = (section, buf)
            else:
                emit(section, buf)
    if carry:  # 文件最後一塊太短：併回前一塊，沒有前一塊就自己一塊
        if chunks:
            chunks[-1].text += f"\n\n【{carry[0]}】{carry[1]}"
        else:
            emit(carry[0], carry[1])
    return chunks
