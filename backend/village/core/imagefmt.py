"""圖片格式：看檔頭判斷（不相信前端或供應商給的 mime）。附圖上傳與生圖結果共用。"""

RASTER = {"image/png", "image/jpeg", "image/webp", "image/gif"}  # SVG 不收：從我們的網址開啟可能執行裡面的程式碼


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None
