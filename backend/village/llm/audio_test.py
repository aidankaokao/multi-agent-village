"""轉寫測驗：送一段內容已知的錄音（macOS 台灣中文語音念的一句話），轉出來的字對得上才算通過。

錄音放在 assets/audio_test.mp3（約 5 秒、19 KB），內容是 EXPECTED。
比對前先統一成繁體、去掉標點與空白、阿拉伯數字換成國字（「3點」和「三點」都算對），
再算兩兩相鄰字的重疊比例：語音辨識難免錯一兩個字，六成以上就算聽得懂中文。
"""

import re
from dataclasses import dataclass
from pathlib import Path

from village.core.audio import to_traditional

AUDIO = Path(__file__).parent / "assets" / "audio_test.mp3"
FILENAME = "audio_test.mp3"
EXPECTED = "今天下午三點，大家在村子的廣場開會。"
PASS_RATIO = 0.6

_DIGITS = str.maketrans("0123456789", "〇一二三四五六七八九")
_NOT_WORD = re.compile(r"[\s\W_]+")


@dataclass
class AudioResult:
    passed: bool
    score: float
    reason: str


def _normalize(text: str) -> str:
    return _NOT_WORD.sub("", to_traditional(text)).translate(_DIGITS)


def _bigrams(text: str) -> set[str]:
    return {text[i : i + 2] for i in range(len(text) - 1)}


def grade(answer: str) -> AudioResult:
    want, got = _bigrams(_normalize(EXPECTED)), _bigrams(_normalize(answer))
    score = len(want & got) / len(want) if want else 0.0
    if not answer.strip():
        return AudioResult(False, 0.0, "模型沒有轉出任何文字")
    if score >= PASS_RATIO:
        return AudioResult(True, score, f"轉寫正確（吻合 {score:.0%}），這個模型可以用來轉逐字稿")
    return AudioResult(False, score, f"轉出來的字和錄音內容差太多（吻合 {score:.0%}，要 {PASS_RATIO:.0%} 以上）")


def audio() -> bytes:
    return AUDIO.read_bytes()
