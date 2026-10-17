"""多輪上下文：前幾輪的問答，以及「這句話是不是在指前面的內容」的判斷。"""

import re
from dataclasses import dataclass

PREV = "prev"  # 計畫裡 use 填 "prev" = 上一輪的回答

# 指稱前面內容的說法：「它、這個、剛剛、上面、再…」
_REFERS = re.compile(r"它|這個|這篇|這首|這段|這些|那個|那首|那篇|剛剛|剛才|上面|上一|前面|前一|再把|再幫|再來|改成|改寫|翻成|\b(it|this|that|above)\b", re.I)


@dataclass
class Turn:
    user: str
    answer: str


def refers_back(message: str) -> bool:
    return bool(_REFERS.search(message))


def render_history(turns: list[Turn]) -> str:
    lines = []
    for i, t in enumerate(turns, 1):
        lines += [f"（第 {i} 輪）使用者：{t.user}", f"（第 {i} 輪）回答：{t.answer}"]
    return "\n".join(lines)
