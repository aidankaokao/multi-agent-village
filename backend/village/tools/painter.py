"""畫家：把請求整理成生圖提示詞，交給 image 分級的生圖模型畫成圖。

LLM 只負責整理提示詞（JSON schema：prompt / aspect_ratio / title / use_reference）；畫圖、存檔、張數上限在 core/painter.py。
有參考圖時（使用者附的圖、上一張畫作），LLM 判斷要「以它為底修改」還是「只參考內容另外畫」。
LLM 不能用時退回「直接用請求的原話畫」。畫一張就要花錢，所以 costly=True（審核員退件不重畫）。
管理員可以在村民設定改「參數整理說明」，例如加上「一律用像素風」當成畫家的固定畫風。
"""

from typing import Any

from village.core.painter import PAINT_ENV
from village.llm.base import LLMError
from village.tools.base import Attachment, Tool, ToolError, ToolResult

ASPECTS = ["1:1", "16:9", "9:16", "4:3", "3:4"]
MAX_PROMPT = 1500


class Painter(Tool):
    id = "painter"
    name = "畫家"
    description = "用生圖模型畫插畫、圖片、海報（需要在 LLM 設定加入 image 分級的生圖模型；不是畫數據圖表）"
    keywords = ["插畫", "插圖", "畫一張圖", "畫一幅", "生成圖片", "產生圖片", "海報", "畫畫", "繪製", "illustration"]
    examples = ["畫一隻在月光下睡覺的貓", "幫這首詩配一張插圖", "把這張照片改成像素風", "把剛剛那張的背景換成海邊"]
    costly = True
    planner_hint = "也能以使用者附的圖或上一張畫作為底修改（改畫風、換背景、加減東西、調色）"
    default_prompt = """你是村子裡畫家的助手，負責把使用者想要的畫面，整理成給生圖模型的提示詞。

規則：
1. prompt 用英文寫，具體描述畫面：主體、動作、場景、光線、色調、構圖、畫風（使用者沒指定畫風時選最適合的）。
2. 只畫使用者要求的內容；參考資料（例如前一位村民寫的詩、鑑定師看到的圖片內容）是作畫的依據，挑出能畫成畫面的元素。
3. 畫面裡需要出現文字時，把文字原樣放在引號裡。
4. aspect_ratio：海報、手機桌布用 9:16，風景、橫幅用 16:9，其他用 1:1；以參考圖修改時，選最接近參考圖形狀的比例。
5. title 用繁體中文，10 個字以內，替這張畫取名。
6. use_reference：下方寫著「這次有參考圖」時才可能是 true。
   - true：使用者要「修改」那張圖（改成某種畫風、換背景、加 / 去掉東西、調亮、換顏色、再改一次…）。
     prompt 寫「要怎麼改」，例如 "Turn this photo into pixel art, keep the composition"、"Change the background to a beach"。
   - false：只是參考內容另外畫一張新的（例如依收據的品項畫插畫），或根本沒有參考圖。
只輸出 JSON：{"prompt": "...", "aspect_ratio": "1:1", "title": "...", "use_reference": false}"""

    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "aspect_ratio": {"type": "string", "enum": ASPECTS},
                "title": {"type": "string"},
                "use_reference": {"type": "boolean"},
            },
            "required": ["prompt", "aspect_ratio", "title", "use_reference"],
            "additionalProperties": False,
        }

    def context(self) -> str:
        env = PAINT_ENV.get()
        if env is None or not env.references:
            return "這次沒有參考圖：use_reference 填 false。"
        refs = "、".join(r.describe() for r in env.references)
        if any(r.source == "painting" for r in env.references):
            return (
                f"這次有參考圖：{refs}。使用者這句話看起來是要修改上一張畫作："
                "除非使用者明確說要重新畫一張新的，use_reference 填 true，prompt 只寫要怎麼改（其他保持不變）。"
            )
        return f"這次有參考圖：{refs}。使用者要修改它時 use_reference 填 true。"

    def _check(self, args: dict[str, Any]) -> tuple[str, str, str]:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise ToolError("prompt 是空的：請描述要畫的畫面")
        aspect = args.get("aspect_ratio") if args.get("aspect_ratio") in ASPECTS else "1:1"
        title = str(args.get("title") or "").strip()[:20] or "畫作"
        return prompt[:MAX_PROMPT], aspect, title

    def run(self, args: dict[str, Any]) -> ToolResult:
        raise ToolError("畫家要用非同步的 arun 畫圖")

    async def arun(self, args: dict[str, Any]) -> ToolResult:
        prompt, aspect, title = self._check(args)
        env = PAINT_ENV.get()
        if env is None:
            raise LLMError("畫家只能在問答或村民試打裡畫圖")
        use_reference = args.get("use_reference") is True and bool(env.references)
        painted = await env.paint(prompt, aspect, use_reference)
        cost = f"，約 ${painted.cost:.3f}" if painted.cost is not None else ""
        done = "依參考圖改好了" if painted.edited else "畫好了"
        note = f"\n（{painted.note}）" if painted.note else ""
        return ToolResult(
            text=f"🎨 {done}「{title}」（圖片附在下方；畫作保存 14 天）{note}\n\n畫面描述：{prompt}",
            attachments=[Attachment(kind="image", name=f"{title}.png", title=title, url=f"/api/images/{painted.image_id}")],
            detail=f"{painted.model}{cost}",
        )

    def fallback(self, instruction: str, context: str) -> dict[str, Any] | None:
        """LLM 整理不出提示詞：直接用請求（加上參考資料的開頭）畫。"""
        text = instruction.strip()
        if context.strip():
            text = f"{text}\n\n{context.strip()[:600]}"
        env = PAINT_ENV.get()
        has_refs = bool(env and env.references)  # 有參考圖時多半是要修改它
        args = {"prompt": text, "aspect_ratio": "1:1", "title": instruction.strip()[:10] or "畫作", "use_reference": has_refs}
        return args if text else None
