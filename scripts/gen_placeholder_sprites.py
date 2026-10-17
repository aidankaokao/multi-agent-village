"""產生村落用的佔位像素角色四向圖（placeholder sprites）。

每個角色輸出一張 sprite sheet：
  - 單格 32x32，3 欄 x 4 列 = 96x128
  - 列順序（RPG Maker 慣例）：0=down, 1=left, 2=right, 3=up
  - 欄順序：0=踏步A, 1=站立, 2=踏步B（走路動畫用 0-1-2-1 循環）

另外輸出 UI 狀態泡泡（頭頂運作中動畫）與一張放大預覽圖。

用法：uv run --with pillow python scripts/gen_placeholder_sprites.py
之後若改用 AI 生成或手繪的正式素材，只要維持相同的 sheet 規格即可直接替換。
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parent.parent
OUT_CHAR = ROOT / "frontend/public/assets/sprites/characters"
OUT_UI = ROOT / "frontend/public/assets/sprites/ui"
OUT_DECO = ROOT / "frontend/public/assets/sprites/deco"
OUT_PREVIEW = ROOT / "docs/images/sprites_preview.png"

FW = FH = 32
DIRS = ["down", "left", "right", "up"]
OUTLINE = (27, 27, 42, 255)
SKIN = (246, 205, 166, 255)
EYE = (40, 30, 40, 255)
BLUSH = (240, 150, 150, 255)


def hexc(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4)) + (255,)


def r(d: ImageDraw.ImageDraw, x0, y0, x1, y1, c):
    d.rectangle([x0, y0, x1, y1], fill=c)


def px(d: ImageDraw.ImageDraw, pts, c):
    for p in pts:
        d.point(p, fill=c)


def add_outline(img: Image.Image) -> Image.Image:
    src = img.load()
    out = img.copy()
    dst = out.load()
    W, H = img.size
    for y in range(H):
        for x in range(W):
            if src[x, y][3] != 0:
                continue
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < W and 0 <= ny < H and src[nx, ny][3] != 0:
                    dst[x, y] = OUTLINE
                    break
    return out


def add_shadow(img: Image.Image) -> Image.Image:
    shadow = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse([9, 28, 22, 31], fill=(0, 0, 0, 70))
    shadow.alpha_composite(img)
    return shadow


# ---------------------------------------------------------------- 基本身體


def draw_body(d, view, frame, pal):
    """view: down / left / up（right 由 left 鏡射）"""
    dy = 0 if frame == 1 else 1
    hair, cloth, cloth2, shoe = pal["hair"], pal["cloth"], pal["cloth2"], pal["shoe"]

    # 腿
    if view in ("down", "up"):
        ll = 28 - (1 if frame == 0 else 0)
        rl = 28 - (1 if frame == 2 else 0)
        r(d, 12, 25, 14, ll, cloth2)
        r(d, 12, ll, 14, ll, shoe)
        r(d, 17, 25, 19, rl, cloth2)
        r(d, 17, rl, 19, rl, shoe)
    else:
        if frame == 1:
            r(d, 14, 25, 17, 28, cloth2)
            r(d, 13, 28, 17, 28, shoe)
        else:
            fx, bx = (11, 17) if frame == 0 else (17, 11)
            r(d, bx, 25, bx + 3, 27, cloth2)
            r(d, bx, 27, bx + 3, 27, shoe)
            r(d, fx, 25, fx + 3, 28, cloth2)
            r(d, fx - 1, 28, fx + 3, 28, shoe)

    # 身體
    if view in ("down", "up"):
        r(d, 10, 18 + dy, 21, 25, cloth)
        la = -1 if frame == 0 else 0
        ra = -1 if frame == 2 else 0
        r(d, 8, 19 + dy + la, 9, 23 + dy + la, cloth)
        r(d, 8, 24 + dy + la, 9, 24 + dy + la, SKIN)
        r(d, 22, 19 + dy + ra, 23, 23 + dy + ra, cloth)
        r(d, 22, 24 + dy + ra, 23, 24 + dy + ra, SKIN)
    else:
        r(d, 11, 18 + dy, 20, 25, cloth)
        sx = {0: -2, 1: 0, 2: 2}[frame]
        r(d, 14 + sx, 19 + dy, 16 + sx, 23 + dy, cloth2)
        r(d, 14 + sx, 24 + dy, 16 + sx, 24 + dy, SKIN)

    # 頭
    if view == "down":
        r(d, 10, 6 + dy, 21, 17 + dy, SKIN)
        r(d, 9, 4 + dy, 22, 8 + dy, hair)
        r(d, 9, 9 + dy, 10, 13 + dy, hair)
        r(d, 21, 9 + dy, 22, 13 + dy, hair)
        r(d, 13, 12 + dy, 13, 13 + dy, EYE)
        r(d, 18, 12 + dy, 18, 13 + dy, EYE)
        px(d, [(12, 15 + dy), (19, 15 + dy)], BLUSH)
        r(d, 15, 15 + dy, 16, 15 + dy, (190, 110, 100, 255))
    elif view == "up":
        r(d, 10, 6 + dy, 21, 17 + dy, SKIN)
        r(d, 9, 4 + dy, 22, 16 + dy, hair)
    else:  # left
        r(d, 10, 6 + dy, 20, 17 + dy, SKIN)
        px(d, [(9, 13 + dy)], SKIN)
        r(d, 9, 4 + dy, 21, 8 + dy, hair)
        r(d, 15, 9 + dy, 21, 15 + dy, hair)
        r(d, 12, 12 + dy, 12, 13 + dy, EYE)
        px(d, [(13, 15 + dy)], BLUSH)
    return dy


# ---------------------------------------------------------------- 各角色配件


def acc_poet(d, view, frame, dy):
    beret, quill = hexc("#7a3fa0"), hexc("#f4f4f4")
    r(d, 8, 2 + dy, 21, 5 + dy, beret)
    px(d, [(15, 1 + dy), (16, 1 + dy)], beret)
    if view == "down":
        px(d, [(24, 16 + dy), (24, 17 + dy), (23, 18 + dy), (23, 19 + dy), (25, 15 + dy)], quill)
    elif view == "left":
        px(d, [(12, 16 + dy), (12, 17 + dy), (13, 18 + dy), (11, 15 + dy)], quill)


def acc_jester(d, view, frame, dy):
    red, yel, bell = hexc("#d8343c"), hexc("#f2c230"), hexc("#ffe680")
    for x in range(9, 23):
        px(d, [(x, 5 + dy), (x, 6 + dy)], red if (x // 2) % 2 == 0 else yel)
    px(d, [(9, 4 + dy), (8, 3 + dy), (7, 2 + dy), (8, 4 + dy)], red)
    px(d, [(6, 1 + dy)], bell)
    px(d, [(22, 4 + dy), (23, 3 + dy), (24, 2 + dy), (23, 4 + dy)], yel)
    px(d, [(25, 1 + dy)], bell)
    if view in ("down", "up"):
        for x in range(10, 22):
            if x % 2 == 0:
                px(d, [(x, 18 + dy)], yel)
        r(d, 16, 19 + dy, 21, 25, yel)


def acc_translator(d, view, frame, dy):
    frame_c, lens, book = hexc("#2a2a3a"), hexc("#cfe3ff"), hexc("#8a4b2a")
    if view == "down":
        for x0 in (12, 17):
            r(d, x0, 11 + dy, x0 + 2, 13 + dy, lens)
            px(d, [(x0 + 1, 12 + dy)], EYE)
            px(d, [(x0, 11 + dy), (x0 + 1, 11 + dy), (x0 + 2, 11 + dy)], frame_c)
        px(d, [(15, 12 + dy), (16, 12 + dy)], frame_c)
        r(d, 12, 20 + dy, 19, 23 + dy, book)
        r(d, 15, 20 + dy, 16, 23 + dy, hexc("#f1e6c8"))
    elif view == "left":
        r(d, 10, 11 + dy, 13, 13 + dy, lens)
        px(d, [(12, 12 + dy)], EYE)
        r(d, 10, 11 + dy, 13, 11 + dy, frame_c)
        r(d, 9, 20 + dy, 12, 23 + dy, book)


def acc_mayor(d, view, frame, dy):
    gold, beard, staff, gem = hexc("#f2c230"), hexc("#f4f4f4"), hexc("#7a4e2a"), hexc("#4fd6e0")
    r(d, 10, 2 + dy, 21, 4 + dy, gold)
    px(d, [(10, 1 + dy), (15, 1 + dy), (16, 1 + dy), (21, 1 + dy)], gold)
    px(d, [(15, 3 + dy), (16, 3 + dy)], hexc("#d8343c"))
    if view == "down":
        r(d, 11, 15 + dy, 20, 19 + dy, beard)
        r(d, 13, 20 + dy, 18, 21 + dy, beard)
        r(d, 15, 22 + dy, 16, 25, gold)
        r(d, 25, 8, 25, 28, staff)
        px(d, [(25, 7), (24, 7), (26, 7), (25, 6)], gem)
    elif view == "left":
        r(d, 9, 15 + dy, 14, 19 + dy, beard)
        r(d, 7, 8, 7, 28, staff)
        px(d, [(7, 7), (6, 7), (8, 7), (7, 6)], gem)
    else:
        r(d, 25, 8, 25, 28, staff)
        px(d, [(25, 7), (24, 7), (26, 7), (25, 6)], gem)


def acc_reviewer(d, view, frame, dy):
    cap, visor, badge, band = hexc("#22305a"), hexc("#11182e"), hexc("#f2c230"), hexc("#d8343c")
    r(d, 9, 2 + dy, 22, 6 + dy, cap)
    if view == "down":
        r(d, 9, 7 + dy, 22, 7 + dy, visor)
        px(d, [(15, 4 + dy), (16, 4 + dy)], badge)
        px(d, [(18, 20 + dy), (19, 20 + dy), (18, 21 + dy), (19, 21 + dy)], badge)
        r(d, 8, 21 + dy, 9, 21 + dy, band)
    elif view == "left":
        r(d, 6, 7 + dy, 14, 7 + dy, visor)
        px(d, [(11, 4 + dy)], badge)
        r(d, 14, 21 + dy, 16, 21 + dy, band)


def acc_craftsman(d, view, frame, dy):
    helmet, belt, metal, handle = hexc("#f2a02c"), hexc("#7a4e2a"), hexc("#b8c0cc"), hexc("#8a5a32")
    r(d, 9, 2 + dy, 22, 6 + dy, helmet)
    r(d, 8, 6 + dy, 23, 6 + dy, helmet)
    if view in ("down", "up"):
        r(d, 10, 23, 21, 23, belt)
    if view == "down":
        px(d, [(15, 3 + dy), (16, 3 + dy)], hexc("#ffd27a"))
        r(d, 24, 14 + dy, 24, 21 + dy, handle)  # 槌子
        r(d, 23, 12 + dy, 26, 14 + dy, metal)
    elif view == "left":
        r(d, 6, 7 + dy, 9, 7 + dy, helmet)
        r(d, 11, 23, 20, 23, belt)
        r(d, 13, 14 + dy, 13, 21 + dy, handle)
        r(d, 11, 12 + dy, 14, 14 + dy, metal)


def acc_scholar(d, view, frame, dy):
    cap, tassel, scroll = hexc("#22222e"), hexc("#f2c230"), hexc("#f1e6c8")
    r(d, 7, 3 + dy, 24, 4 + dy, cap)
    r(d, 10, 5 + dy, 21, 6 + dy, cap)
    px(d, [(22, 5 + dy), (22, 6 + dy), (22, 7 + dy)], tassel)
    if view == "down":
        r(d, 17, 20 + dy, 21, 22 + dy, scroll)
        px(d, [(17, 20 + dy), (17, 22 + dy), (21, 20 + dy), (21, 22 + dy)], hexc("#c8a060"))
    elif view == "left":
        r(d, 9, 20 + dy, 12, 22 + dy, scroll)


def acc_merchant(d, view, frame, dy):
    hat, band, bag, coin = hexc("#6b4226"), hexc("#d8343c"), hexc("#a8763e"), hexc("#f2c230")
    r(d, 6, 5 + dy, 25, 6 + dy, hat)
    r(d, 10, 1 + dy, 21, 5 + dy, hat)
    r(d, 10, 4 + dy, 21, 4 + dy, band)
    if view == "down":
        r(d, 18, 21 + dy, 22, 25, bag)
        px(d, [(20, 20 + dy)], coin)
        px(d, [(19, 23), (21, 23)], coin)
    elif view == "left":
        r(d, 15, 21 + dy, 19, 25, bag)
        px(d, [(17, 23)], coin)


def acc_farmer(d, view, frame, dy):
    straw, straw2, stalk = hexc("#e8c86a"), hexc("#c9a442"), hexc("#5aa04a")
    r(d, 5, 5 + dy, 26, 6 + dy, straw)
    r(d, 10, 2 + dy, 21, 5 + dy, straw)
    r(d, 10, 5 + dy, 21, 5 + dy, straw2)
    if view == "down":
        r(d, 7, 14 + dy, 7, 24 + dy, stalk)  # 稻穗
        px(d, [(6, 13 + dy), (8, 13 + dy), (7, 12 + dy), (6, 15 + dy), (8, 15 + dy)], straw)
    elif view == "left":
        r(d, 18, 14 + dy, 18, 24 + dy, stalk)
        px(d, [(17, 13 + dy), (19, 13 + dy), (18, 12 + dy)], straw)


def acc_chef(d, view, frame, dy):
    white, shade, spoon = hexc("#fafafa"), hexc("#d8d8e0"), hexc("#b8c0cc")
    r(d, 10, 0 + dy, 21, 5 + dy, white)
    r(d, 9, 1 + dy, 22, 3 + dy, white)
    r(d, 10, 5 + dy, 21, 6 + dy, shade)
    if view in ("down", "up"):
        r(d, 12, 19 + dy, 19, 25, white)  # 圍裙
    if view == "down":
        r(d, 24, 15 + dy, 24, 22 + dy, spoon)
        r(d, 23, 13 + dy, 25, 15 + dy, spoon)
    elif view == "left":
        r(d, 12, 19 + dy, 17, 25, white)


def acc_auditor(d, view, frame, dy):
    collar, glass, rim, handle = hexc("#f4f4f4"), hexc("#cfe3ff"), hexc("#c8a060"), hexc("#7a4e2a")
    r(d, 9, 3 + dy, 22, 5 + dy, hexc("#2a2a3a"))  # 方帽
    if view in ("down", "up"):
        r(d, 13, 18 + dy, 18, 19 + dy, collar)
        px(d, [(15, 20 + dy), (16, 20 + dy)], collar)
    if view == "down":
        r(d, 22, 12 + dy, 26, 16 + dy, rim)  # 放大鏡
        r(d, 23, 13 + dy, 25, 15 + dy, glass)
        r(d, 22, 17 + dy, 22, 20 + dy, handle)
    elif view == "left":
        r(d, 7, 12 + dy, 11, 16 + dy, rim)
        r(d, 8, 13 + dy, 10, 15 + dy, glass)
        r(d, 11, 17 + dy, 11, 20 + dy, handle)


def acc_scribe(d, view, frame, dy):
    book, page, quill, hood = hexc("#7a3a2a"), hexc("#f1e6c8"), hexc("#f4f4f4"), hexc("#5a3a22")
    r(d, 9, 3 + dy, 22, 5 + dy, hood)  # 頭巾
    px(d, [(8, 6 + dy), (23, 6 + dy)], hood)
    if view == "down":
        r(d, 11, 20 + dy, 18, 24 + dy, book)  # 筆記本
        r(d, 12, 21 + dy, 17, 23 + dy, page)
        r(d, 14, 21 + dy, 14, 23 + dy, book)
        px(d, [(21, 17 + dy), (22, 16 + dy), (23, 15 + dy), (24, 14 + dy), (20, 18 + dy)], quill)
    elif view == "left":
        r(d, 8, 20 + dy, 12, 24 + dy, book)
        r(d, 9, 21 + dy, 11, 23 + dy, page)


def acc_appraiser(d, view, frame, dy):
    hat, band, gold, lens = hexc("#2a3a2e"), hexc("#a8323e"), hexc("#e0b84a"), hexc("#cfe3ff")
    sky, hill = hexc("#7ab8e8"), hexc("#4a9a4a")
    r(d, 10, 1 + dy, 21, 4 + dy, hat)  # 圓頂禮帽
    r(d, 8, 5 + dy, 23, 5 + dy, hat)
    r(d, 10, 4 + dy, 21, 4 + dy, band)
    if view == "down":
        r(d, 17, 11 + dy, 19, 13 + dy, lens)  # 單片眼鏡
        px(d, [(18, 12 + dy)], EYE)
        px(d, [(17, 10 + dy), (18, 10 + dy), (19, 10 + dy), (20, 11 + dy), (20, 12 + dy), (20, 13 + dy), (17, 14 + dy), (18, 14 + dy), (19, 14 + dy), (16, 11 + dy), (16, 12 + dy), (16, 13 + dy)], gold)
        px(d, [(20, 14 + dy), (20, 15 + dy), (19, 16 + dy), (19, 17 + dy)], gold)  # 鏈子
        r(d, 10, 19 + dy, 19, 25, gold)  # 手上的畫框
        r(d, 11, 20 + dy, 18, 22 + dy, sky)
        r(d, 11, 23 + dy, 18, 24, hill)
    elif view == "left":
        r(d, 10, 11 + dy, 12, 13 + dy, lens)
        px(d, [(11, 12 + dy)], EYE)
        px(d, [(9, 11 + dy), (9, 12 + dy), (9, 13 + dy), (10, 10 + dy), (11, 10 + dy), (12, 10 + dy), (10, 14 + dy), (11, 14 + dy), (12, 14 + dy)], gold)
        r(d, 6, 19 + dy, 12, 25, gold)
        r(d, 7, 20 + dy, 11, 22 + dy, sky)
        r(d, 7, 23 + dy, 11, 24, hill)


CHARACTERS = {
    # id: (中文名, 外觀說明（工匠挑外觀時參考）, 調色盤, 配件, 系統角色？)
    "poet": ("詩人", "貝雷帽、羽毛筆，文藝創作", dict(hair="#5a3a22", cloth="#3f8f5a", cloth2="#2c6640", shoe="#3a2a20"), acc_poet, False),
    "jester": ("笑話師", "小丑帽，幽默娛樂", dict(hair="#e08a2c", cloth="#d8343c", cloth2="#7a2a8a", shoe="#3a2a20"), acc_jester, False),
    "translator": ("翻譯官", "眼鏡、書本，語言文字", dict(hair="#1e1e2a", cloth="#3a6fc4", cloth2="#28477d", shoe="#2a2a2a"), acc_translator, False),
    "reviewer": ("巡查員", "制服帽、徽章，檢查把關", dict(hair="#3b2a1e", cloth="#2b3a67", cloth2="#1b2442", shoe="#111111"), acc_reviewer, False),
    "scholar": ("學者", "學士帽、卷軸，知識研究、教學解說", dict(hair="#4a3426", cloth="#5a4a8a", cloth2="#3a2f5e", shoe="#2a2a2a"), acc_scholar, False),
    "merchant": ("商人", "寬簷帽、錢袋，商業計算、買賣", dict(hair="#2a1e14", cloth="#2f7f7a", cloth2="#1f5552", shoe="#3a2a20"), acc_merchant, False),
    "farmer": ("農夫", "草帽、稻穗，生活實用、自然", dict(hair="#5a3a22", cloth="#7a9a3a", cloth2="#4a6a2a", shoe="#3a2a20"), acc_farmer, False),
    "chef": ("廚師", "高帽、湯匙，料理食譜", dict(hair="#3a2a1e", cloth="#e8e8ee", cloth2="#3a3a4a", shoe="#222222"), acc_chef, False),
    "mayor": ("村長", "王冠、法杖", dict(hair="#c8c8c8", cloth="#a8323e", cloth2="#6e1f28", shoe="#2a1a1a"), acc_mayor, True),
    "craftsman": ("工匠", "安全帽、槌子", dict(hair="#3a2a1e", cloth="#4a6a8a", cloth2="#2a3a4a", shoe="#2a1a1a"), acc_craftsman, True),
    "scribe": ("書記", "頭巾、筆記本、羽毛筆", dict(hair="#3a2a1e", cloth="#8a6a3a", cloth2="#5a4424", shoe="#2a1a1a"), acc_scribe, True),
    "appraiser": ("鑑定師", "圓頂禮帽、單片眼鏡、畫框", dict(hair="#d8d0c0", cloth="#6a3a7a", cloth2="#46264f", shoe="#1a1a1a"), acc_appraiser, True),
    "auditor": ("審核員", "黑袍、放大鏡", dict(hair="#5a4a3a", cloth="#2a2a3a", cloth2="#1a1a26", shoe="#111111"), acc_auditor, True),
}


# ---------------------------------------------------------------- 地圖裝飾


def _canvas(w, h):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _ground_shadow(img, cx, w):
    """在底部加一個橢圓陰影（畫在物件後面）"""
    W, H = img.size
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([cx - w // 2, H - 5, cx + w // 2, H - 1], fill=(0, 0, 0, 70))
    sh.alpha_composite(img)
    return sh


def deco_tree():
    img, d = _canvas(32, 48)
    trunk, trunk2 = hexc("#7a4e2a"), hexc("#5a3820")
    g1, g2, g3 = hexc("#2f7a3a"), hexc("#3f9a48"), hexc("#6cc060")
    r(d, 14, 32, 18, 45, trunk)
    r(d, 17, 32, 18, 45, trunk2)
    d.ellipse([3, 4, 29, 34], fill=g1)
    d.ellipse([5, 3, 26, 28], fill=g2)
    d.ellipse([8, 6, 18, 15], fill=g3)
    px(d, [(22, 12), (24, 18), (10, 22), (19, 25)], g1)
    return _ground_shadow(add_outline(img), 16, 18)


def deco_pine():
    img, d = _canvas(32, 48)
    trunk, g1, g2 = hexc("#6a4224"), hexc("#1f5e3a"), hexc("#2f7f4a")
    r(d, 14, 38, 17, 45, trunk)
    for i, (top, half) in enumerate([(2, 6), (10, 10), (19, 13)]):
        for y in range(top, top + 14):
            w = min(half, (y - top) * half // 9 + 1)
            r(d, 16 - w, y, 15 + w, y, g1 if i % 2 else g2)
            px(d, [(16 - w + 1, y)], hexc("#4f9f5a"))
    return _ground_shadow(add_outline(img), 16, 16)


def deco_bush():
    img, d = _canvas(32, 24)
    g1, g2, berry = hexc("#2f7a3a"), hexc("#4aa850"), hexc("#d8343c")
    d.ellipse([3, 6, 29, 22], fill=g1)
    d.ellipse([6, 4, 22, 17], fill=g2)
    px(d, [(10, 12), (19, 9), (23, 15), (14, 17)], berry)
    return _ground_shadow(add_outline(img), 16, 24)


def deco_flowers():
    img, d = _canvas(32, 20)
    stem = hexc("#2f7a3a")
    for x, y, c in [(5, 10, "#f2c230"), (12, 6, "#e86a92"), (19, 11, "#ffffff"), (26, 7, "#e86a92"), (14, 14, "#9a7af2"), (24, 15, "#f2c230")]:
        r(d, x, y + 2, x, y + 4, stem)
        col = hexc(c)
        px(d, [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)], col)
        px(d, [(x, y)], hexc("#f9e07a") if c != "#f2c230" else hexc("#d8843c"))
    return img


def deco_rock():
    img, d = _canvas(24, 16)
    d.ellipse([2, 3, 22, 15], fill=hexc("#8a8f9e"))
    d.ellipse([5, 3, 15, 10], fill=hexc("#aeb3c0"))
    px(d, [(16, 11), (9, 12)], hexc("#6c7080"))
    return _ground_shadow(add_outline(img), 12, 20)


def deco_well():
    img, d = _canvas(32, 44)
    stone, stone2, wood, roof, water = hexc("#9aa0ae"), hexc("#7a8090"), hexc("#7a4e2a"), hexc("#a8323e"), hexc("#3a6fc4")
    r(d, 6, 28, 25, 41, stone)
    for y in (31, 35, 39):
        for x in range(6 if y % 8 else 9, 25, 6):
            r(d, x, y, x + 3, y + 1, stone2)
    r(d, 8, 27, 23, 29, water)
    r(d, 7, 12, 8, 28, wood)
    r(d, 23, 12, 24, 28, wood)
    for i in range(6):
        r(d, 4 + i, 11 - i, 27 - i, 11 - i, roof)
    r(d, 8, 16, 23, 16, wood)  # 橫桿
    r(d, 15, 17, 16, 22, hexc("#c8a060"))  # 繩子
    return _ground_shadow(add_outline(img), 16, 26)


def _house(roof_hex, roof2_hex):
    img, d = _canvas(64, 64)
    wall, wall2, wood, glass, roof, roof2 = hexc("#e8d6b0"), hexc("#d0bc94"), hexc("#7a4e2a"), hexc("#9fd0f0"), hexc(roof_hex), hexc(roof2_hex)
    r(d, 8, 30, 55, 60, wall)
    r(d, 8, 30, 10, 60, wall2)
    for i in range(18):  # 階梯狀屋頂
        r(d, 4 + i, 30 - i, 59 - i, 30 - i, roof if (i // 3) % 2 else roof2)
    r(d, 44, 6, 49, 18, hexc("#8a5a42"))  # 煙囪
    r(d, 27, 44, 36, 60, wood)
    px(d, [(34, 52)], hexc("#f2c230"))
    for x0 in (14, 42):
        r(d, x0, 38, x0 + 8, 46, glass)
        r(d, x0 + 4, 38, x0 + 4, 46, wood)
        r(d, x0, 42, x0 + 8, 42, wood)
    return _ground_shadow(add_outline(img), 32, 54)


def deco_house_red():
    return _house("#b8423e", "#d8564e")


def deco_house_blue():
    return _house("#3a5f9c", "#4f78b8")


def deco_sign():
    img, d = _canvas(24, 32)
    wood, wood2 = hexc("#8a5a32"), hexc("#a8763e")
    r(d, 11, 12, 13, 30, wood)
    r(d, 2, 4, 21, 13, wood2)
    r(d, 5, 7, 18, 7, hexc("#5a3820"))
    r(d, 5, 10, 14, 10, hexc("#5a3820"))
    return _ground_shadow(add_outline(img), 12, 10)


def deco_workbench():
    img, d = _canvas(32, 28)
    wood, wood2, metal = hexc("#a8763e"), hexc("#7a4e2a"), hexc("#b8c0cc")
    r(d, 2, 10, 29, 14, wood)
    r(d, 4, 15, 6, 26, wood2)
    r(d, 25, 15, 27, 26, wood2)
    r(d, 8, 20, 23, 21, wood2)
    r(d, 6, 6, 12, 9, metal)  # 槌頭
    r(d, 9, 9, 9, 9, wood2)
    r(d, 17, 7, 26, 9, metal)  # 鋸子
    r(d, 24, 5, 27, 9, wood2)
    return _ground_shadow(add_outline(img), 16, 30)


DECOR = {
    "tree": deco_tree,
    "pine": deco_pine,
    "bush": deco_bush,
    "flowers": deco_flowers,
    "rock": deco_rock,
    "well": deco_well,
    "house_red": deco_house_red,
    "house_blue": deco_house_blue,
    "sign": deco_sign,
    "workbench": deco_workbench,
}


def render_frame(pal, acc, view, frame) -> Image.Image:
    img = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    dy = draw_body(d, view, frame, pal)
    acc(d, view, frame, dy)
    return add_shadow(add_outline(img))


def build_sheet(pal_hex, acc) -> Image.Image:
    pal = {k: hexc(v) for k, v in pal_hex.items()}
    sheet = Image.new("RGBA", (FW * 3, FH * 4), (0, 0, 0, 0))
    for row, direction in enumerate(DIRS):
        for col in range(3):
            if direction == "right":
                f = ImageOps.mirror(render_frame(pal, acc, "left", col))
            else:
                f = render_frame(pal, acc, direction, col)
            sheet.alpha_composite(f, (col * FW, row * FH))
    return sheet


# ---------------------------------------------------------------- 頭頂狀態泡泡


def build_status_bubbles() -> Image.Image:
    """16x16 一格。第 0 列：working 動畫 4 格；第 1 列：error / done / thinking / idle(zzz)"""
    S = 16
    sheet = Image.new("RGBA", (S * 4, S * 2), (0, 0, 0, 0))
    white, gray, dark = (255, 255, 255, 255), (170, 170, 190, 255), (40, 40, 60, 255)

    def bubble():
        im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        r(d, 2, 1, 13, 10, white)
        r(d, 1, 2, 14, 9, white)
        px(d, [(4, 11), (5, 11), (3, 12)], white)
        return im, d

    def finish(im):
        src = im.load()
        out = im.copy()
        dst = out.load()
        for y in range(S):
            for x in range(S):
                if src[x, y][3] == 0 and any(
                    0 <= nx < S and 0 <= ny < S and src[nx, ny][3] and src[nx, ny] == white
                    for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
                ):
                    dst[x, y] = dark
        return out

    for i in range(4):
        im, d = bubble()
        for j, x in enumerate((3, 7, 11)):
            c = dark if (i == 3 or j == i) else gray
            oy = -1 if j == i else 0
            r(d, x, 5 + oy, x + 1, 6 + oy, c)
        sheet.alpha_composite(finish(im), (i * S, 0))

    im, d = bubble()  # error
    r(d, 7, 2, 8, 6, (216, 52, 60, 255))
    r(d, 7, 8, 8, 8, (216, 52, 60, 255))
    sheet.alpha_composite(finish(im), (0, S))
    im, d = bubble()  # done
    px(d, [(4, 6), (5, 7), (6, 8), (7, 7), (8, 6), (9, 5), (10, 4), (11, 3)], (60, 170, 90, 255))
    px(d, [(4, 5), (5, 6), (6, 7), (7, 6), (8, 5), (9, 4), (10, 3)], (60, 170, 90, 255))
    sheet.alpha_composite(finish(im), (S, S))
    im, d = bubble()  # thinking (?)
    px(d, [(6, 3), (7, 2), (8, 2), (9, 3), (9, 4), (8, 5), (7, 6), (7, 8)], (58, 111, 196, 255))
    sheet.alpha_composite(finish(im), (2 * S, S))
    im, d = bubble()  # idle zzz
    px(d, [(4, 4), (5, 4), (6, 4), (5, 5), (4, 6), (5, 6), (6, 6)], gray)
    px(d, [(8, 3), (9, 3), (10, 3), (9, 4), (8, 5), (9, 5), (10, 5)], dark)
    sheet.alpha_composite(finish(im), (3 * S, S))
    return sheet


def main():
    OUT_CHAR.mkdir(parents=True, exist_ok=True)
    OUT_UI.mkdir(parents=True, exist_ok=True)
    OUT_PREVIEW.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "frameWidth": FW,
        "frameHeight": FH,
        "rows": DIRS,
        "walkCycle": [0, 1, 2, 1],
        "characters": {},
        "ui": {"status_bubbles": {"path": "ui/status_bubbles.png", "frameWidth": 16, "frameHeight": 16,
                                  "working": [0, 1, 2, 3], "error": 4, "done": 5, "thinking": 6, "idle": 7}},
    }
    sheets = []
    for cid, (name, desc, pal, acc, system) in CHARACTERS.items():
        sheet = build_sheet(pal, acc)
        sheet.save(OUT_CHAR / f"{cid}.png")
        manifest["characters"][cid] = {"name": name, "desc": desc, "path": f"characters/{cid}.png"}
        if system:  # 系統角色（村長、工匠）不能給村民用
            manifest["characters"][cid]["system"] = True
        sheets.append(sheet)

    OUT_DECO.mkdir(parents=True, exist_ok=True)
    manifest["decor"] = {}
    decos = []
    for did, fn in DECOR.items():
        im = fn()
        im.save(OUT_DECO / f"{did}.png")
        manifest["decor"][did] = {"path": f"deco/{did}.png", "width": im.width, "height": im.height}
        decos.append(im)

    bubbles = build_status_bubbles()
    bubbles.save(OUT_UI / "status_bubbles.png")
    (OUT_CHAR.parent / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    # 預覽：所有角色並排 + 泡泡，放大 4 倍
    gap = 8
    w = sum(s.width for s in sheets) + gap * (len(sheets) + 1)
    deco_w = sum(im.width for im in decos) + gap * (len(decos) + 1)
    w = max(w, deco_w)
    h = FH * 4 + gap * 4 + bubbles.height + 64
    prev = Image.new("RGBA", (w, h), (120, 170, 110, 255))
    x = gap
    for s in sheets:
        prev.alpha_composite(s, (x, gap))
        x += s.width + gap
    prev.alpha_composite(bubbles, (gap, FH * 4 + gap * 2))
    x = bubbles.width + gap * 3
    for im in decos:
        prev.alpha_composite(im, (x, h - gap - im.height))
        x += im.width + gap
    prev.resize((w * 4, h * 4), Image.NEAREST).save(OUT_PREVIEW)
    print(f"wrote {len(sheets)} character sheets, status bubbles, manifest, preview -> {OUT_PREVIEW}")


if __name__ == "__main__":
    main()
