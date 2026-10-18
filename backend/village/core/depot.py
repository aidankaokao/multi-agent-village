"""中轉站：步驟之間交接檔案的地方（像店家把外送餐點放在取餐區、貼上號碼）。

每一步產生的檔案（聽打員的逐字稿、文件彙整師的報告 PDF…）放進中轉站並編上取件號碼（#1、#2…），
內容本身留在 chat_files，中轉站只記「號碼、檔名、誰放的、在哪裡」。
後面的步驟用到那一步的成果（計畫的 use）時，村子只用文字通知它「中轉站有給你的 #1 逐字稿.txt」，
要交給駐村專家的才在出發前取件（讀出內容放進 A2A file part；對方在別的容器，連不回村子來拿）。
只有文字成果的村民（摘要師、網路研究員）在後面的收件者要檔案時，才把文字包成 txt 放進中轉站。

每次問答各自一個中轉站（context variable `DEPOT`；Runner 是全村共用的）。
"""

import mimetypes
import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import PurePath

from village.core.docfiles import MIME, DocFile, safe_name

FILE_URL = "/api/files/"
Loader = Callable[[str], Awaitable[bytes | None]]  # 檔案 id → 內容（找不到 / 過期回 None）


@dataclass
class Parcel:
    tag: int  # 取件號碼
    name: str
    mime: str
    size: int | None
    sender: str  # 放的人（村民 id）
    sender_name: str
    step: str  # 哪一步產生的
    file_id: str | None = None  # 存在 chat_files 的（有下載連結）
    data: bytes | None = None  # 只在這次問答裡的（例如文字成果包成的 txt）

    @property
    def ext(self) -> str:
        return PurePath(self.name).suffix.lower()

    def label(self) -> str:
        size = f"，{_size(self.size)}" if self.size else ""
        return f"#{self.tag} {self.name}（來自{self.sender_name}{size}）"

    def to_dict(self) -> dict:
        return {"tag": self.tag, "name": self.name, "sender": self.sender, "step": self.step}


def _size(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, round(n / 1024))} KB"


@dataclass
class Depot:
    load: Loader | None = None
    parcels: list[Parcel] = field(default_factory=list)

    def drop(self, sender: str, sender_name: str, step: str, name: str, *, file_id: str | None = None,
             data: bytes | None = None, mime: str | None = None, size: int | None = None) -> Parcel:
        mime = mime or MIME.get(PurePath(name).suffix.lower()) or mimetypes.guess_type(name)[0] or "application/octet-stream"
        p = Parcel(len(self.parcels) + 1, name, mime, size if size is not None else (len(data) if data else None),
                   sender, sender_name, step, file_id, data)
        self.parcels.append(p)
        return p

    def drop_attachments(self, sender: str, sender_name: str, step: str, attachments: list[dict]) -> list[Parcel]:
        """一步做完：它產生的檔案（附件 kind=file）放進中轉站。"""
        out = []
        for a in attachments:
            url = str(a.get("url") or "")
            if a.get("kind") == "file" and url.startswith(FILE_URL):
                out.append(self.drop(sender, sender_name, step, str(a.get("name") or "檔案"), file_id=url[len(FILE_URL):]))
        return out

    def drop_text(self, sender: str, sender_name: str, step: str, text: str, about: str = "") -> Parcel:
        """只有文字成果：包成 txt（同一步只包一次）。檔名帶上那一步在做什麼，好幾位研究員的成果才分得出來。"""
        for p in self.parcels:
            if p.step == step and p.data is not None and p.ext == ".txt":
                return p
        about = re.sub(r"[\s，。、,.:：;；!?！？「」『』（）()]+", "", about)[:16]
        name = f"{sender_name}_{about}.txt" if about else f"{sender_name}的成果.txt"
        return self.drop(sender, sender_name, step, safe_name(name), data=text.encode(), mime=MIME[".txt"])

    def from_steps(self, step_ids: list[str]) -> list[Parcel]:
        return [p for p in self.parcels if p.step in step_ids]

    async def pick_up(self, parcels: list[Parcel]) -> list[DocFile]:
        """取件：讀出內容（交給駐村專家用）。過期或被刪掉的略過。"""
        out = []
        for p in parcels:
            data = p.data
            if data is None and p.file_id and self.load:
                data = await self.load(p.file_id)
            if data:
                out.append(DocFile(p.name, p.mime, data))
        return out


def slip(parcels: list[Parcel]) -> str:
    """取件通知（給收件的村民看的文字）。"""
    if not parcels:
        return ""
    return "中轉站有前面步驟交給你的檔案（已經替你取來，附在這則訊息）：" + "、".join(p.label() for p in parcels)


DEPOT: ContextVar[Depot | None] = ContextVar("depot", default=None)


@contextmanager
def depot(d: Depot | None) -> Iterator[None]:
    token = DEPOT.set(d)
    try:
        yield
    finally:
        DEPOT.reset(token)
