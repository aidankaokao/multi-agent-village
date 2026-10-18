"""錄音檔：格式檢查、用 ffmpeg 轉成小檔並切段、逐字稿排版（段落時間戳、SRT 字幕）、簡轉繁。

雲端轉寫（OpenAI 等）單檔上限 25 MB，gpt-4o 系列也有長度上限，所以一律先用 ffmpeg 轉成
單聲道 16 kHz、48 kbps 的 mp3（一小時約 21 MB），再切成每段 10 分鐘，各段同時送出。
格式看副檔名＋檔頭（不相信瀏覽器給的 mime）。
"""

import asyncio
import csv
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePath

from village.llm.base import LLMError, Segment

MB = 1024 * 1024
MAX_AUDIO_FILES = 3
MAX_AUDIO_BYTES = 200 * MB
CHUNK_SECONDS = 600
FFMPEG_TIMEOUT = 600

MIME = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
}
EXTS = list(MIME)


class AudioError(ValueError):
    """訊息直接給使用者看。"""


def is_audio_name(name: str) -> bool:
    return PurePath(name).suffix.lower() in MIME


def looks_like_audio(ext: str, head: bytes) -> bool:
    """檔頭檢查：擋掉改了副檔名的其他檔案。"""
    if ext == ".mp3":
        return head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)
    if ext in (".m4a", ".mp4"):
        return head[4:8] == b"ftyp"
    if ext == ".wav":
        return head.startswith(b"RIFF") and head[8:12] == b"WAVE"
    if ext == ".webm":
        return head.startswith(b"\x1a\x45\xdf\xa3")
    if ext in (".ogg", ".opus"):
        return head.startswith(b"OggS")
    if ext == ".flac":
        return head.startswith(b"fLaC")
    if ext == ".aac":
        return head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xF6 == 0xF0)
    return False


def check_head(name: str, head: bytes) -> str:
    """回傳 mime；不合格 raise AudioError。"""
    ext = PurePath(name).suffix.lower()
    if ext not in MIME:
        raise AudioError(f"「{name}」不是支援的錄音格式；可以附 {'、'.join(EXTS)}")
    if not head:
        raise AudioError(f"「{name}」是空檔案")
    if not looks_like_audio(ext, head):
        raise AudioError(f"「{name}」看起來不是有效的 {ext[1:]} 錄音檔")
    return MIME[ext]


def size_text(n: int) -> str:
    return f"{n / MB:.1f} MB" if n >= MB else f"{max(1, round(n / 1024))} KB"


def clock(seconds: float, hours: bool = False) -> str:
    s = max(0, int(seconds))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h or hours else f"{m:02d}:{sec:02d}"


def length_text(seconds: float) -> str:
    s = max(1, round(seconds))
    if s < 60:
        return f"{s} 秒"
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h} 小時 {m} 分" if h else f"{m} 分 {sec} 秒"


# ---------------------------------------------------------------- 簡轉繁

_CC = None


def to_traditional(text: str) -> str:
    """轉成台灣繁體與用語（軟件 → 軟體），中文旁邊的半形標點改成全形（「,」→「，」）；本來就是繁體的不會變。

    有假名的（日文）不轉，免得把日文漢字改掉。
    """
    global _CC
    if not re.search(r"[\u4e00-\u9fff]", text) or re.search(r"[\u3040-\u30ff]", text):
        return text
    if _CC is None:
        from opencc import OpenCC

        _CC = OpenCC("s2twp")
    return _HALF_PUNCT.sub(lambda m: _FULL[m.group(0)], _CC.convert(text))


_FULL = {",": "，", "?": "？", "!": "！", ":": "：", ";": "；"}
# 前面或後面是中文的半形標點（「好,我們」「24%,還在」）；數字裡的逗號（1,200）、英文句子不動
_HALF_PUNCT = re.compile(
    r"(?<=[\u4e00-\u9fff\u3001-\u303f\uff01-\uff5e])[,?!:;]|[,?!:;](?=[\u4e00-\u9fff])"
)


# ---------------------------------------------------------------- ffmpeg


@dataclass
class Chunk:
    path: Path
    start: float  # 這段在原始錄音的起點（秒）
    end: float


async def _run(*args: str) -> str:
    if shutil.which(args[0]) is None:
        raise LLMError(f"伺服器沒有安裝 {args[0]}，沒辦法處理錄音")
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), FFMPEG_TIMEOUT)
    except (TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        tail = err.decode(errors="replace").strip().splitlines()[-1:] or ["（沒有訊息）"]
        raise AudioError(f"錄音檔讀不了（{tail[0][:200]}）")
    return out.decode(errors="replace")


async def duration_of(path: Path) -> float:
    out = await _run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path))
    try:
        return float(out.strip())
    except ValueError:
        raise AudioError("讀不出錄音長度（檔案可能損壞）") from None


async def split(path: Path, workdir: Path, chunk_seconds: int = CHUNK_SECONDS) -> list[Chunk]:
    """轉成單聲道 16 kHz mp3 並切段；回傳每段的檔案與在原始錄音的起訖時間。"""
    out_dir = workdir / "chunks"
    out_dir.mkdir()
    listing = workdir / "chunks.csv"
    await _run(
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k",
        "-f", "segment", "-segment_time", str(chunk_seconds), "-reset_timestamps", "1",
        "-segment_list", str(listing), "-segment_list_type", "csv", str(out_dir / "%03d.mp3"),
    )
    chunks: list[Chunk] = []
    with listing.open(newline="") as f:
        for row in csv.reader(f):
            if len(row) >= 3 and (p := out_dir / row[0]).exists() and p.stat().st_size > 0:
                chunks.append(Chunk(p, float(row[1]), float(row[2])))
    if not chunks:
        raise AudioError("錄音裡沒有聲音軌")
    return chunks


# ---------------------------------------------------------------- 排版

_CJK = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]")  # 中日文字與全形標點
_SENTENCE_END = re.compile(r"(?<=[。！？!?])")
PARAGRAPH_SECONDS = 60
PARAGRAPH_CHARS = 300


def join(parts: list[str]) -> str:
    """中文不加空白，英文用空白接。"""
    out = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and not (_CJK.search(out[-1]) or _CJK.search(p[0])):
            out += " "
        out += p
    return out


def paragraphs(segments: list[Segment]) -> list[tuple[float, str]]:
    """把細碎的段落合成大約一分鐘一段，每段帶開頭的時間。"""
    out: list[tuple[float, str]] = []
    start, buf = 0.0, []
    for s in segments:
        if not buf:
            start = s.start
        buf.append(s.text)
        if s.end - start >= PARAGRAPH_SECONDS or len(join(buf)) >= PARAGRAPH_CHARS:
            out.append((start, join(buf)))
            buf = []
    if buf:
        out.append((start, join(buf)))
    return out


def split_text(text: str, start: float) -> list[tuple[float | None, str]]:
    """沒有時間戳的模型：依句號切成約 300 字一段，只有第一段標時間（這一段錄音的起點）。"""
    out: list[tuple[float | None, str]] = []
    buf = ""
    for sentence in _SENTENCE_END.split(text):
        buf += sentence
        if len(buf) >= PARAGRAPH_CHARS:
            out.append((None, buf.strip()))
            buf = ""
    if buf.strip():
        out.append((None, buf.strip()))
    if out:
        out[0] = (start, out[0][1])
    return out


def render_paragraphs(paras: list[tuple[float | None, str]], hours: bool) -> str:
    return "\n\n".join(f"〔{clock(t, hours)}〕{text}" if t is not None else text for t, text in paras if text)


def _srt_time(t: float) -> str:
    ms = max(0, round(t * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


def to_srt(segments: list[Segment]) -> str:
    return "\n".join(
        f"{i}\n{_srt_time(s.start)} --> {_srt_time(max(s.end, s.start + 0.5))}\n{s.text}\n"
        for i, s in enumerate(segments, 1)
    )
