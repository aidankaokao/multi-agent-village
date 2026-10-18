"""問答附的文件（xlsx / csv / txt / pdf）：格式檢查、大小上限，以及哪些駐村專家收哪些檔案。

村子自己不讀文件內容：文件只交給名片上寫著「收這類檔案」的駐村專家（例如文件彙整師），其他村民看不到。
格式看副檔名＋檔頭（不相信瀏覽器給的 mime）。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePath

MB = 1024 * 1024
MAX_FILES = 3
MAX_FILE_BYTES = 10 * MB
MAX_TOTAL_BYTES = 20 * MB

MIME = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
}
EXT_OF_MIME = {m: e for e, m in MIME.items()}
LABEL = {".xlsx": "Excel", ".csv": "CSV", ".txt": "文字檔", ".pdf": "PDF"}


class DocError(ValueError):
    """訊息直接給使用者看。"""


@dataclass
class DocFile:
    name: str
    mime: str
    data: bytes

    @property
    def ext(self) -> str:
        return PurePath(self.name).suffix.lower()

    def describe(self) -> str:
        return f"{self.name}（{LABEL.get(self.ext, self.ext)}，{_size(len(self.data))}）"


def _size(n: int) -> str:
    return f"{n / MB:.1f} MB" if n >= MB else f"{max(1, round(n / 1024))} KB"


def safe_name(name: str) -> str:
    name = PurePath(name.replace("\\", "/")).name.strip()  # 去掉路徑
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*')
    return name[-120:] or "文件"


def check(name: str, data: bytes) -> DocFile:
    """單一檔案：副檔名、大小、檔頭。"""
    name = safe_name(name)
    ext = PurePath(name).suffix.lower()
    if ext not in MIME:
        hint = "（舊版 .xls 請另存成 .xlsx）" if ext == ".xls" else ""
        raise DocError(f"「{name}」的格式不支援{hint}；可以附 {'、'.join(MIME)}")
    if not data:
        raise DocError(f"「{name}」是空檔案")
    if len(data) > MAX_FILE_BYTES:
        raise DocError(f"「{name}」有 {_size(len(data))}，超過每份 {_size(MAX_FILE_BYTES)} 的上限")
    if ext == ".pdf" and not data[:1024].lstrip().startswith(b"%PDF"):
        raise DocError(f"「{name}」不是有效的 PDF")
    if ext == ".xlsx" and not data.startswith(b"PK"):
        raise DocError(f"「{name}」不是有效的 xlsx（舊版 .xls 改副檔名也不行，請用 Excel 另存成 .xlsx）")
    if ext in (".csv", ".txt") and b"\x00" in data[:4096] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise DocError(f"「{name}」看起來不是文字檔")
    return DocFile(name, MIME[ext], data)


def check_all(files: Iterable[tuple[str, bytes]]) -> list[DocFile]:
    docs = [check(n, d) for n, d in files]
    if len(docs) > MAX_FILES:
        raise DocError(f"一次最多附 {MAX_FILES} 份文件")
    total = sum(len(d.data) for d in docs)
    if total > MAX_TOTAL_BYTES:
        raise DocError(f"文件合計 {_size(total)}，超過 {_size(MAX_TOTAL_BYTES)} 的上限")
    return docs


def accepts_from_card(card: dict) -> list[str]:
    """駐村專家收哪些文件（副檔名）：先看 x-file-limits.extensions，再看技能 / 預設的 inputModes（mime）。

    text/plain 是每個 agent 都收的「文字訊息」，只看 mime 時不能當成收 txt 檔。
    """
    limits = card.get("x-file-limits") if isinstance(card.get("x-file-limits"), dict) else {}
    exts = [str(e).lower() for e in limits.get("extensions") or []]
    if not exts:
        mimes: list[str] = list(card.get("defaultInputModes") or [])
        for s in card.get("skills") or []:
            if isinstance(s, dict):
                mimes += s.get("inputModes") or []
        exts = [EXT_OF_MIME[m] for m in mimes if m in EXT_OF_MIME and m != "text/plain"]
    return sorted({e for e in exts if e in MIME}, key=list(MIME).index)


MAX_AGENT_FILES = 10  # 不管名片寫多少，一次最多交給駐村專家這麼多份


def max_files_from_card(card: dict) -> int:
    limits = card.get("x-file-limits") if isinstance(card.get("x-file-limits"), dict) else {}
    n = limits.get("maxFiles")
    return max(1, min(MAX_AGENT_FILES, n)) if isinstance(n, int) and not isinstance(n, bool) else MAX_FILES


def for_agent(docs: list[DocFile], accepts: list[str]) -> list[DocFile]:
    return [d for d in docs if d.ext in accepts]
