"""Event bus：後端每個動作發一個事件，透過 WebSocket 推給前端驅動村落動畫。"""

import asyncio
from datetime import UTC, datetime
from typing import Any


# 別人的問答事件只留下村落動畫需要的欄位（誰在動、走向誰），內容（問題、輸出、答案）不外流
PUBLIC_FIELDS = ("type", "ts", "agent", "from_agent", "to_agent", "passed")


def for_viewer(event: dict, user_id: str) -> dict:
    owner = event.get("user_id")
    if owner is None or owner == user_id:
        return event
    return {k: event[k] for k in PUBLIC_FIELDS if k in event}


class EventBus:
    def __init__(self, queue_size: int = 256):
        self._subs: set[asyncio.Queue] = set()
        self._queue_size = queue_size
        self._owners: dict[str, str] = {}  # run_id → user_id：這次問答的事件只有提問者看得到內容

    def claim(self, run_id: str, user_id: str | None) -> None:
        if user_id:
            self._owners[run_id] = user_id

    def release(self, run_id: str) -> None:
        self._owners.pop(run_id, None)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def publish(self, type_: str, **data: Any) -> None:
        event = {"type": type_, "ts": datetime.now(UTC).isoformat(), **data}
        if (owner := self._owners.get(data.get("run_id") or "")) is not None:
            event["user_id"] = owner
        for q in list(self._subs):
            if q.full():  # 慢的訂閱者丟掉最舊的事件，不卡住整個系統
                q.get_nowait()
            q.put_nowait(event)
