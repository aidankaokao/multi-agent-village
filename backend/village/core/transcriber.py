"""聽打員的轉寫環境：這次問答附的錄音 → ffmpeg 切段 → audio 分級的模型同時轉寫各段 → 排版、存逐字稿檔案。

聽打員是工具型村民（工具 `typist`，在 `tools/typist.py`）：LLM 只整理「什麼語言、有哪些專有名詞」，真正轉寫在這裡。
Runner 是全村共用的，所以「這次問答是誰、附了哪些錄音」放在 context variable（TRANSCRIBE_ENV），由問答（api/chat.py）設定。
錄音轉完就刪掉（只留逐字稿）；逐字稿檔案（txt、srt）跟駐村專家的報告一樣保存 14 天。
"""

import asyncio
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

from village.core import audio
from village.llm.base import LLMError, Segment
from village.llm.service import AUDIO

PARALLEL = 3  # 同時轉寫幾段
TXT_MIME = "text/plain; charset=utf-8"
SRT_MIME = "application/x-subrip"

# 每分鐘大約多少美元（只是估計，供應商沒有回報費用；不在表上的不顯示）
PRICE_PER_MINUTE = {"whisper-1": 0.006, "gpt-4o-transcribe": 0.006, "gpt-4o-mini-transcribe": 0.003}

Progress = Callable[[str], Awaitable[None]]


@dataclass
class AudioFile:
    id: str
    name: str
    data: bytes

    def describe(self) -> str:
        return f"{self.name}（{audio.size_text(len(self.data))}）"


@dataclass
class Transcribed:
    name: str
    duration: float
    text: str  # 排好版的逐字稿（段落開頭有時間）
    model: str
    file_ids: list[tuple[str, str]] = field(default_factory=list)  # [(檔名, 檔案 id)]
    timestamps: bool = True  # 模型有沒有給每句的時間（沒有就只有每 10 分鐘一個時間）

    @property
    def cost(self) -> float | None:
        rate = PRICE_PER_MINUTE.get(self.model)
        return rate * self.duration / 60 if rate is not None else None


@dataclass
class TranscribeEnv:
    llm: Any  # LLMService（要有 transcribe）
    files: list[AudioFile]
    max_minutes: int  # 村規：每份錄音最長幾分鐘
    save_file: Callable[[str, str, bytes], Awaitable[str]] | None = None  # 存逐字稿，回傳檔案 id
    discard: Callable[[str], Awaitable[None]] | None = None  # 轉完刪掉錄音
    cache: dict[str, Transcribed] = field(default_factory=dict)  # 已經轉好的（重做時不再轉一次、不再花錢）

    async def transcribe_all(self, language: str | None, prompt: str, progress: Progress | None = None) -> list[Transcribed]:
        if not self.files:
            raise LLMError("這次沒有附錄音檔：請用 📎 附上 mp3、m4a、wav 等錄音再問")
        out: list[Transcribed] = []
        for f in self.files:
            if f.id in self.cache:
                out.append(self.cache[f.id])
                continue
            t = await self._one(f, language, prompt, progress, len(out) + 1)
            self.cache[f.id] = t
            out.append(t)
            if self.discard:
                await self.discard(f.id)
        return out

    async def _one(self, f: AudioFile, language: str | None, prompt: str, progress: Progress | None, n: int) -> Transcribed:
        label = f"「{f.name}」" if len(self.files) == 1 else f"第 {n} 份「{f.name}」"
        with tempfile.TemporaryDirectory(prefix="typist-") as tmp:
            work = Path(tmp)
            src = work / ("input" + PurePath(f.name).suffix.lower())
            src.write_bytes(f.data)
            duration = await audio.duration_of(src)
            if duration > self.max_minutes * 60:
                raise LLMError(
                    f"{label}有 {audio.length_text(duration)}，超過村規每份 {self.max_minutes} 分鐘的上限"
                    "（管理員可以在「村規」調整）"
                )
            if progress:
                await progress(f"⏳ 準備{label}（{audio.length_text(duration)}）…")
            chunks = await audio.split(src, work)
            finished = 0
            gate = asyncio.Semaphore(PARALLEL)

            async def run(c: audio.Chunk):
                nonlocal finished
                async with gate:
                    r = await self.llm.transcribe(f"tier:{AUDIO}", c.path.read_bytes(), c.path.name,
                                                  language=language, prompt=prompt or None)
                finished += 1
                if progress and len(chunks) > 1:
                    await progress(f"⏳ 轉寫{label}：{finished} / {len(chunks)} 段…")
                return r

            results = await asyncio.gather(*(run(c) for c in chunks))
        hours = duration >= 3600
        segments: list[Segment] = []
        paras: list[tuple[float | None, str]] = []
        timestamps = all(r.segments for r in results if r.text)
        for c, r in zip(chunks, results, strict=True):
            if timestamps:
                segments += [Segment(c.start + s.start, c.start + s.end, audio.to_traditional(s.text)) for s in r.segments]
            else:
                paras += audio.split_text(audio.to_traditional(r.text), c.start)
        if timestamps:
            paras = list(audio.paragraphs(segments))
        text = audio.render_paragraphs(paras, hours).strip()
        if not text:
            raise LLMError(f"{label}沒有轉出任何文字（可能是沒有人說話，或語言設定不對）")
        model = next((r.model for r in results if r.model), "")
        t = Transcribed(f.name, duration, text, model, timestamps=timestamps)
        if self.save_file:
            stem = PurePath(f.name).stem[:80] or "錄音"
            header = f"{f.name}（{audio.length_text(duration)}，{model}）逐字稿\n\n"
            t.file_ids.append((f"{stem}_逐字稿.txt", await self.save_file(f"{stem}_逐字稿.txt", TXT_MIME, (header + text + "\n").encode())))
            if segments:
                t.file_ids.append((f"{stem}_字幕.srt", await self.save_file(f"{stem}_字幕.srt", SRT_MIME, audio.to_srt(segments).encode())))
        return t


TRANSCRIBE_ENV: ContextVar[TranscribeEnv | None] = ContextVar("transcribe_env", default=None)


@contextmanager
def transcribing(env: TranscribeEnv | None) -> Iterator[None]:
    token = TRANSCRIBE_ENV.set(env)
    try:
        yield
    finally:
        TRANSCRIBE_ENV.reset(token)
