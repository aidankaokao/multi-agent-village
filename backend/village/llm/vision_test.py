"""看圖測驗：當場畫一張答案已知的圖，請模型說出圖上的數字和圖形顏色，兩個都對才算「真的看得到圖」。

有些模型收到圖片不會報錯，而是忽略圖片、編一個答案，所以不能只看「有沒有回應」。
數字每次隨機（4 位數，用猜的幾乎不可能對），顏色從 4 種裡挑。
圖用純 Python 產生 PNG（不用 Pillow）：5x7 點陣數字放大。
"""

import base64
import random
import re
import struct
import zlib
from dataclasses import dataclass

from village.llm.base import ImagePart

W, H = 360, 200
SCALE = 7  # 點陣字放大倍數
BG = (255, 255, 255)
INK = (20, 20, 20)

COLORS: dict[str, tuple[tuple[int, int, int], tuple[str, ...]]] = {
    "紅": ((220, 40, 40), ("紅", "red")),
    "藍": ((40, 90, 220), ("藍", "blue")),
    "綠": ((30, 150, 60), ("綠", "green")),
    "紫": ((140, 60, 190), ("紫", "purple", "violet")),
}

# 5x7 點陣數字（每列 5 bit，最高位在左）
FONT = {
    "0": [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
    "1": [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
    "2": [0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F],
    "3": [0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E],
    "4": [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
    "5": [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
    "6": [0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E],
    "7": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
    "8": [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
    "9": [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C],
}

PROMPT = """請仔細看這張圖片，回答兩件事：
1. 圖上寫的四位數字是多少？
2. 左邊的圖形是什麼顏色？（紅、藍、綠、紫 其中一個）
如果你看不到圖片，請直接說「看不到圖片」，不要猜。
用一句話回答，格式：數字 ____，____色"""


@dataclass
class VisionQuiz:
    number: str
    color: str
    image: ImagePart


@dataclass
class QuizResult:
    passed: bool
    number_ok: bool
    color_ok: bool
    reason: str


def _png(pixels: list[bytearray]) -> bytes:
    raw = b"".join(b"\x00" + bytes(row) for row in pixels)  # 每列前面的 filter type = 0

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)  # 8-bit RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def draw(number: str, color: str, shape: str = "circle") -> bytes:
    rgb = COLORS[color][0]
    pixels = [bytearray(BG * W) for _ in range(H)]

    def put(x: int, y: int, c: tuple[int, int, int]) -> None:
        if 0 <= x < W and 0 <= y < H:
            pixels[y][x * 3 : x * 3 + 3] = bytes(c)

    # 左邊的圖形
    cx, cy, r = 80, H // 2, 50
    for y in range(cy - r, cy + r + 1):
        for x in range(cx - r, cx + r + 1):
            if shape == "square" or (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                put(x, y, rgb)
    # 右邊的數字
    x0, y0 = 160, (H - 7 * SCALE) // 2
    for i, ch in enumerate(number):
        for row, bits in enumerate(FONT[ch]):
            for col in range(5):
                if bits & (1 << (4 - col)):
                    for dy in range(SCALE):
                        for dx in range(SCALE):
                            put(x0 + i * 6 * SCALE + col * SCALE + dx, y0 + row * SCALE + dy, INK)
    return _png(pixels)


def make_quiz(rng: random.Random | None = None) -> VisionQuiz:
    rng = rng or random.SystemRandom()
    number = str(rng.randint(1000, 9999))
    color = rng.choice(list(COLORS))
    png = draw(number, color, rng.choice(["circle", "square"]))
    return VisionQuiz(number=number, color=color, image=ImagePart("image/png", base64.b64encode(png).decode()))


def grade(quiz: VisionQuiz, answer: str) -> QuizResult:
    text = re.sub(r"(?<=\d)[\s,，、.]+(?=\d)", "", answer)  # 「4 7 2 9」「4,729」也算
    number_ok = quiz.number in re.findall(r"\d+", text)
    lower = answer.lower()
    said = {c for c, (_, words) in COLORS.items() if any(w in lower for w in words)}
    color_ok = said == {quiz.color}  # 只說了正確的顏色（同時列出好幾個顏色不算）
    passed = number_ok and color_ok
    if passed:
        reason = "數字和顏色都答對，這個模型看得到圖片"
    elif not number_ok and not color_ok:
        reason = "數字和顏色都不對：這個模型可能看不到圖片（或忽略了圖片）"
    elif not number_ok:
        reason = f"數字不對（正確是 {quiz.number}）：可能看不清楚圖上的文字"
    else:
        reason = f"顏色不對（正確是{quiz.color}色）"
    return QuizResult(passed=passed, number_ok=number_ok, color_ok=color_ok, reason=reason)
