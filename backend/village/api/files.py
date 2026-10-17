"""問答附的文件：上傳格式檢查、下載（只有本人拿得到）。

上傳的文件（xlsx / csv / txt / pdf）跟著對話保存；駐村專家回傳的報告（PDF）14 天過期。
下載一律當附件（Content-Disposition: attachment），不在瀏覽器裡直接打開。
"""

import base64
import binascii
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from village.core.docfiles import MAX_FILE_BYTES, MAX_FILES, DocError, DocFile, check_all
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
