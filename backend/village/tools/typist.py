"""聽打員：把使用者附的錄音轉成逐字稿（audio 分級的語音轉文字模型，例如 OpenAI whisper-1）。

LLM 只整理參數（JSON schema：language / terms）：錄音是什麼語言、有哪些人名或專有名詞（交給轉寫模型當提示，比較不會聽錯）。
真正的切段、轉寫、排版、存檔在 core/transcriber.py。LLM 不能用時退回「中文、沒有專有名詞」。
只做逐字稿，不做摘要：要重點、報告就由村長再排下一位村民（逐字稿全文會交給它）。
轉寫按分鐘計費，所以 costly=True（審核員退件不重轉）。
"""

from typing import Any

from village.core import audio
from village.core.transcriber import TRANSCRIBE_ENV
from village.llm.base import LLMError
from village.tools.base import Attachment, Tool, ToolResult, report

LANGUAGES = ["zh", "en", "ja", "ko", "auto"]
LANGUAGE_NAMES = {"zh": "中文", "en": "英文", "ja": "日文", "ko": "韓文"}
MAX_TERMS = 300


class Typist(Tool):
    id = "typist"
    name = "聽打員"
    description = "把使用者附的錄音檔（會議、訪談、課程）轉成逐字稿（需要在 LLM 設定加入 audio 分級的語音轉文字模型）"
    keywords = ["逐字稿", "聽打", "錄音", "轉文字", "語音轉文字", "音檔", "會議記錄", "字幕"]
    examples = ["把這段錄音轉成逐字稿", "幫我聽打這段會議錄音", "把訪談錄音轉成文字，再整理重點"]
    costly = True
    timeout_s = 1800
    planner_hint = "只有它聽得到使用者附的錄音；只做逐字稿，要摘要、重點、報告請再排下一位村民處理它的逐字稿"
    default_prompt = """你是村子裡聽打員的助手，負責判斷錄音的語言，並整理出錄音裡可能出現的專有名詞。

規則：
1. language：錄音的主要語言。使用者沒說時看請求用的語言（繁體中文的請求通常是中文錄音）；
   中文 zh、英文 en、日文 ja、韓文 ko；中英夾雜或不確定就填 auto。
2. terms：請求或參考資料裡出現的人名、公司名、產品名、專有名詞，用頓號隔開（幫轉寫模型聽對這些字）；沒有就填空字串。
   不要自己編造請求裡沒有的名詞。
只輸出 JSON：{"language": "zh", "terms": ""}"""

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": LANGUAGES},
                "terms": {"type": "string"},
            },
            "required": ["language", "terms"],
            "additionalProperties": False,
        }

    def context(self) -> str:
        env = TRANSCRIBE_ENV.get()
        if env is None or not env.files:
            return "這次沒有附錄音檔。"
        return f"這次附的錄音：{'、'.join(f.describe() for f in env.files)}。"

    def run(self, args: dict[str, Any]) -> ToolResult:
        raise LLMError("聽打員要用非同步的 arun 轉寫")

    async def arun(self, args: dict[str, Any]) -> ToolResult:
        env = TRANSCRIBE_ENV.get()
        if env is None:
            raise LLMError("聽打員只能在問答裡轉寫使用者附的錄音")
        language: str = args["language"] if args.get("language") in LANGUAGES else "zh"
        terms = str(args.get("terms") or "").strip()[:MAX_TERMS]
        # 提示用繁體寫：轉寫模型會模仿提示的字體（之後還會再轉一次繁體，雙重保險）
        prompt = "以下是繁體中文的逐字稿。" if language in ("zh", "auto") else ""
        if terms:
            prompt += f"可能出現的名詞：{terms}。"
        done = await env.transcribe_all(None if language == "auto" else language, prompt, report)
        blocks, files, notes = [], [], []
        for t in done:
            blocks.append(f"## {t.name}（{audio.length_text(t.duration)}）\n\n{t.text}" if len(done) > 1 else t.text)
            files += [Attachment(kind="file", name=name, url=f"/api/files/{fid}") for name, fid in t.file_ids]
            if not t.timestamps:
                notes.append(f"{t.model} 沒有提供每句的時間，逐字稿只標出每 10 分鐘的起點，也沒有字幕檔")
        total = sum(t.duration for t in done)
        head = (
            f"📝 逐字稿（{'、'.join(t.name for t in done)}，共 {audio.length_text(total)}"
            f"{'' if language == 'auto' else '，' + LANGUAGE_NAMES[language]}）"
        )
        tail = "\n\n（逐字稿檔案附在下方，保存 14 天；錄音已刪除）" if files else "\n\n（錄音已刪除）"
        note = "".join(f"\n（{n}）" for n in dict.fromkeys(notes))
        costs = [t.cost for t in done]
        cost = f"，約 ${sum(c for c in costs if c is not None):.3f}" if all(c is not None for c in costs) else ""
        return ToolResult(
            text=f"{head}\n\n" + "\n\n".join(blocks) + tail + note,
            attachments=files,
            detail=f"{', '.join(dict.fromkeys(t.model for t in done))}・{audio.length_text(total)}{cost}",
            model=", ".join(dict.fromkeys(t.model for t in done)),
        )

    def fallback(self, instruction: str, context: str) -> dict[str, Any] | None:
        return {"language": "zh", "terms": ""}
