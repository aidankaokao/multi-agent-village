"""問答附圖：上傳格式檢查、讀取圖片（只有本人看得到）。

前端會先縮圖（長邊 ≤ 1568px）並重新編碼成 JPEG（順便去掉 GPS 等 EXIF），這裡只做把關：
格式看檔案開頭判斷（不相信前端給的 mime）、大小上限、張數上限（村規）。
"""

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from village.core.imagefmt import sniff
from village.security.auth import CurrentUser, require_user

router = APIRouter(prefix="/api/images", tags=["images"])

MAX_BYTES = 2 * 1024 * 1024  # 縮圖後通常 100～500KB
HARD_MAX_IMAGES = 10


class ImageIn(BaseModel):
    data: str = Field(max_length=MAX_BYTES * 4 // 3 + 8)  # base64，可以帶 data:...;base64, 前綴


def decode(images: list[ImageIn], max_images: int) -> list[tuple[str, bytes]]:
    """檢查並解碼；不合格就 422。回傳 [(mime, bytes)]。"""
    if len(images) > max_images:
        raise HTTPException(422, f"一次最多附 {max_images} 張圖片" if max_images else "目前村規不開放附圖")
    out: list[tuple[str, bytes]] = []
    for i, img in enumerate(images, 1):
        b64 = img.data.split(",", 1)[1] if img.data.startswith("data:") else img.data
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, f"第 {i} 張圖片的格式不對") from None
        if len(data) > MAX_BYTES:
            raise HTTPException(422, f"第 {i} 張圖片太大（上限 {MAX_BYTES // 1024 // 1024}MB）")
        mime = sniff(data)
        if mime is None:
            raise HTTPException(422, f"第 {i} 張不是支援的圖片（PNG、JPEG、WebP、GIF）")
        out.append((mime, data))
    return out


@router.get("/{image_id}")
async def get_image(image_id: str, request: Request, me: CurrentUser = Depends(require_user)) -> Response:
    img = await request.app.state.ctx.conversations.get_image(image_id, me.id)
    if img is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到這張圖片")
    return Response(
        img.data,
        media_type=img.mime,
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )
