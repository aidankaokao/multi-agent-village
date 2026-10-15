"""Harness：輸出清理、驗證、內容守門。與個別 agent 無關，由 executor 統一套用。"""

import re

from village.core.card import Checks

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_OPEN_THINK = re.compile(r"<think>.*", re.S | re.I)

# 中文詞直接比對子字串；刻意不放單字「幹」以免誤傷「幹嘛、能幹、幹部」
PROFANITY_ZH = [
    "幹你", "幹您", "幹他", "幹妳", "他媽的", "你媽的", "媽的", "靠北", "靠杯", "王八蛋",
    "雞掰", "機掰", "雞巴", "操你", "去死", "婊子", "賤人", "混蛋", "智障", "腦殘", "屁眼",
]
PROFANITY_EN = re.compile(r"\b(fuck\w*|shit\w*|bitch\w*|asshole|bastard|cunt|dick(head)?|motherfucker)\b", re.I)


def normalize(text: str) -> str:
    """清掉本地模型常混進 content 的 <think> 區塊與前後空白。"""
    return _THINK.sub("", text).strip()


def preview(text: str) -> str:
    """串流中的半成品給前端看：清掉 <think>（含還沒結束的），髒話先遮蔽。最終結果仍以 check_output 為準。"""
    text = _OPEN_THINK.sub("", _THINK.sub("", text))
    for k in range(len("<think>") - 1, 0, -1):  # 結尾剛好是半個 <think>（例如 "<thi"）先藏起來
        if text.lower().endswith("<think>"[:k]):
            text = text[:-k]
            break
    return mask_profanity(text.strip())


def profanity_hits(text: str) -> list[str]:
    hits = [w for w in PROFANITY_ZH if w in text]
    hits += [m.group(0) for m in PROFANITY_EN.finditer(text)]
    return hits


def mask_profanity(text: str) -> str:
    for w in PROFANITY_ZH:
        text = text.replace(w, "＊" * len(w))
    return PROFANITY_EN.sub(lambda m: "*" * len(m.group(0)), text)


def check_output(text: str, checks: Checks) -> str | None:
    """回傳給 agent 的修正回饋；None 代表通過。"""
    if checks.must_not_be_empty and not text:
        return "你的回覆是空的。請直接輸出完成的內容。"
    if checks.max_chars and len(text) > checks.max_chars:
        return f"內容太長（{len(text)} 字），請精簡到 {checks.max_chars} 字以內，只輸出內容本身。"
    if hits := profanity_hits(text):
        return f"內容含有不適當的用語（{'、'.join(hits)}），請改寫並避免這類字詞。"
    return None
