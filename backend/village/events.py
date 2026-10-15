"""Event bus：後端每個動作發一個事件，透過 WebSocket 推給前端驅動村落動畫。"""

import asyncio
from datetime import UTC, datetime
from typing import Any


class EventBus:
    def __init__(self, queue_size: int = 256):
        self._subs: set[asyncio.Queue] = set()
        self._queue_size = queue_size

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def publish(self, type_: str, **data: Any) -> None:
        event = {"type": type_, "ts": datetime.now(UTC).isoformat(), **data}
        for q in list(self._subs):
            if q.full():  # 慢的訂閱者丟掉最舊的事件，不卡住整個系統
                q.get_nowait()
            q.put_nowait(event)
