"""執行紀錄（trace）：每次問答寫一筆 runs，執行中就先建立，結束時補上結果。"""

import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from village.store.models import RunTrace

log = logging.getLogger("village.trace")


class TraceStore:
    def __init__(self, sessionmaker: async_sessionmaker, keep: int = 500):
        self._sm = sessionmaker
        self.keep = keep

    async def start(self, run_id: str, message: str) -> None:
        async with self._sm() as s:
            s.add(RunTrace(id=run_id, message=message[:4000], status="running"))
            await s.commit()

    async def finish(self, run_id: str, **fields) -> None:
        """fields：status、answer、error、plan、steps、reviews、elapsed_ms。token 由 steps 加總。"""
        steps = fields.get("steps") or []
        fields["tokens_in"] = sum(st.get("tokens_in") or 0 for st in steps) + fields.pop("extra_tokens_in", 0)
        fields["tokens_out"] = sum(st.get("tokens_out") or 0 for st in steps) + fields.pop("extra_tokens_out", 0)
        try:
            async with self._sm() as s:
                await s.execute(update(RunTrace).where(RunTrace.id == run_id).values(finished_at=datetime.now(UTC), **fields))
                await self._prune(s)
                await s.commit()
        except Exception:  # 紀錄寫不進去不能影響問答
            log.exception("寫入執行紀錄失敗：%s", run_id)

    async def _prune(self, s) -> None:
        cutoff = await s.scalar(select(RunTrace.created_at).order_by(RunTrace.created_at.desc()).offset(self.keep).limit(1))
        if cutoff is not None:
            await s.execute(delete(RunTrace).where(RunTrace.created_at <= cutoff))

    async def mark_interrupted(self) -> int:
        """啟動時：上次沒跑完（伺服器重啟）的標成 interrupted。"""
        async with self._sm() as s:
            r = await s.execute(
                update(RunTrace).where(RunTrace.status == "running").values(status="interrupted", error="伺服器重新啟動，執行中斷")
            )
            await s.commit()
            return r.rowcount or 0
