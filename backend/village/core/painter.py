"""畫家的作畫環境：用 image 分級的生圖模型畫圖、檢查格式、存成畫作（14 天後過期）、守住村規的張數上限。

畫家是工具型村民（工具 `painter`，在 `tools/painter.py`）：LLM 只把請求整理成生圖提示詞，真正畫圖在這裡。
Runner 是全村共用的，所以「這次問答是誰、哪段對話、已經畫了幾張」放在 context variable（PAINT_ENV），
問答（api/chat.py）與村民試打（api/villagers.py）各自設定；沒設定時畫家不能畫。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from village.core.imagefmt import RASTER, sniff
from village.llm.base import GeneratedImage, LLMError
from village.llm.service import IMAGE

if TYPE_CHECKING:
    from village.store.conversations import ConversationStore


def check_generated(images: list[GeneratedImage]) -> list[tuple[str, bytes]]:
    """只收點陣圖：看檔頭判斷（不相信供應商給的 media_type）；SVG 可能帶程式碼，不收。"""
    out = [(mime, img.data) for img in images if (mime := sniff(img.data)) in RASTER]
    if not out:
        kinds = ", ".join(sorted({i.mime or "未知" for i in images}))
        raise LLMError(f"回傳的不是支援的圖片格式（{kinds}）；向量圖（SVG）模型不能用")
    return out


@dataclass
class Painted:
    image_id: str
    model: str
    cost: float | None


@dataclass
class PaintEnv:
    llm: Any  # LLMService（要有 generate_image）
    store: "ConversationStore"
    user_id: str
    conversation_id: str | None
    per_run: int  # 村規：每次問答最多畫幾張
    per_day: int  # 村規：每人每天（最近 24 小時）最多畫幾張
    used: int = 0  # 這次問答已經畫了幾張

    async def paint(self, prompt: str, aspect_ratio: str | None = None) -> Painted:
        if self.per_run <= 0 or self.per_day <= 0:
            raise LLMError("村規目前不開放畫圖（管理員可以在「村規」調整畫家的張數上限）")
        if self.used >= self.per_run:
            raise LLMError(f"這次問答已經畫了 {self.used} 張，達到村規上限（每次 {self.per_run} 張）")
        since = datetime.now(UTC) - timedelta(days=1)
        if (n := await self.store.count_generated_since(self.user_id, since)) >= self.per_day:
            raise LLMError(f"最近 24 小時已經畫了 {n} 張，達到村規上限（每人每天 {self.per_day} 張），晚點再來")
        self.used += 1  # 先記上：生圖失敗也可能已經計費，不讓同一次問答一直重試
        r = await self.llm.generate_image(f"tier:{IMAGE}", prompt, aspect_ratio=aspect_ratio)
        [first, *_] = check_generated(r.images)
        [image_id] = await self.store.save_generated(self.user_id, self.conversation_id, [first])
        return Painted(image_id=image_id, model=r.model, cost=r.cost)


PAINT_ENV: ContextVar[PaintEnv | None] = ContextVar("paint_env", default=None)


@contextmanager
def painting(env: PaintEnv | None) -> Iterator[None]:
    token = PAINT_ENV.set(env)
    try:
        yield
    finally:
        PAINT_ENV.reset(token)
