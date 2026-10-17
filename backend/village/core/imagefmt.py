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


def dimensions(data: bytes) -> tuple[int, int] | None:
    """讀 PNG / JPEG 的寬高（修圖時沿用參考圖的比例）；讀不出來回 None。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # 沒有長度的標記
                i += 2
                continue
            length = int.from_bytes(data[i + 2 : i + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # SOF：高、寬
                return int.from_bytes(data[i + 7 : i + 9], "big"), int.from_bytes(data[i + 5 : i + 7], "big")
            i += 2 + length
    return None
