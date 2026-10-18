"""問答附的文件：上傳格式檢查、下載（只有本人拿得到）。

上傳的文件（xlsx / csv / txt / pdf）跟著對話保存；駐村專家回傳的報告（PDF）14 天過期。
錄音太大，不塞進問答請求：先 POST /api/files/audio（原始內容當 body、檔名放 query），問答時只帶檔案 id。
下載一律當附件（Content-Disposition: attachment），不在瀏覽器裡直接打開。
"""

import base64
import binascii
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from village.core import audio
from village.core.docfiles import MAX_FILE_BYTES, MAX_FILES, DocError, DocFile, check_all, safe_name
from village.llm.service import NO_AUDIO
from village.security.auth import CurrentUser, require_user

router = APIRouter(prefix="/api/files", tags=["files"])


class FileIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    data: str = Field(max_length=MAX_FILE_BYTES * 4 // 3 + 200)  # base64，可以帶 data:...;base64, 前綴


def decode(files: list[FileIn]) -> list[DocFile]:
    """檢查並解碼；不合格就 422。"""
    if len(files) > MAX_FILES:
        raise HTTPException(422, f"一次最多附 {MAX_FILES} 份文件")
    raw: list[tuple[str, bytes]] = []
    for f in files:
        b64 = f.data.split(",", 1)[1] if f.data.startswith("data:") else f.data
        try:
            raw.append((f.name, base64.b64decode(b64, validate=True)))
        except (binascii.Error, ValueError):
            raise HTTPException(422, f"「{f.name}」的內容格式不對") from None
    try:
        return check_all(raw)
    except DocError as e:
        raise HTTPException(422, str(e)) from None


TYPIST_TOOL = "typist"


def typist_of(registry):
    """名冊裡負責轉寫的村民（工具型、工具是 typist）；停用或刪掉了就是 None。"""
    return next((a for a in registry.all() if a.kind == "tool" and a.tool and a.tool.id == TYPIST_TOOL), None)


async def audio_problem(ctx, user_id: str) -> str | None:
    """現在能不能轉寫錄音；不能就回傳原因（直接給使用者看）。"""
    if ctx.rules.current.transcribe_max_minutes <= 0:
        return "村規目前不開放轉寫錄音（管理員可以在「村規」調整聽打員的錄音長度上限）"
    if typist_of(ctx.registry) is None:
        return "聽打員目前停用中：請管理員到村民頁啟用聽打員"
    if not await ctx.llm.has_audio(user_id):
        return NO_AUDIO
    return None


@router.get("/audio/ready")
async def audio_ready(request: Request, me: CurrentUser = Depends(require_user)):
    """附錄音前先問：能不能轉寫（不能就不用白白上傳幾十 MB）。"""
    problem = await audio_problem(request.app.state.ctx, me.id)
    return {"ok": problem is None, "reason": problem, "max_bytes": audio.MAX_AUDIO_BYTES, "extensions": audio.EXTS,
            "max_minutes": request.app.state.ctx.rules.current.transcribe_max_minutes}


@router.post("/audio", status_code=status.HTTP_201_CREATED)
async def upload_audio(
    request: Request, name: str = Query(min_length=1, max_length=200), me: CurrentUser = Depends(require_user)
):
    """上傳一份錄音（body 是檔案原始內容）。邊收邊數大小，超過上限立刻停。"""
    ctx = request.app.state.ctx
    if problem := await audio_problem(ctx, me.id):
        raise HTTPException(status.HTTP_409_CONFLICT, problem)
    name = safe_name(name)
    if not audio.is_audio_name(name):
        raise HTTPException(422, f"「{name}」不是支援的錄音格式；可以附 {'、'.join(audio.EXTS)}")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > audio.MAX_AUDIO_BYTES:
        raise HTTPException(413, f"「{name}」超過每份 {audio.size_text(audio.MAX_AUDIO_BYTES)} 的上限")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > audio.MAX_AUDIO_BYTES:
            raise HTTPException(413, f"「{name}」超過每份 {audio.size_text(audio.MAX_AUDIO_BYTES)} 的上限")
    try:
        mime = audio.check_head(name, bytes(buf[:64]))
    except audio.AudioError as e:
        raise HTTPException(422, str(e)) from None
    file_id = await ctx.conversations.save_audio(me.id, name, mime, bytes(buf))
    return {"id": file_id, "name": name, "size": len(buf)}


@router.get("/{file_id}")
async def get_file(file_id: str, request: Request, me: CurrentUser = Depends(require_user)) -> Response:
    f = await request.app.state.ctx.conversations.get_file(file_id, me.id)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到這個檔案（報告保存 14 天，可能已經過期）")
    return Response(
        f.data,
        media_type=f.mime,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(f.name)}",
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )
