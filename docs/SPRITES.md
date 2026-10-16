# 像素素材規格與生成 Prompt

## 現有佔位素材

由 `scripts/gen_placeholder_sprites.py` 程式產生（`python3 scripts/gen_placeholder_sprites.py`）：

```
frontend/public/assets/sprites/
├── manifest.json               # 規格與角色清單，前端讀這個；desc 給工匠挑外觀參考，system 代表系統角色
├── characters/
│   ├── poet.png                # 詩人
│   ├── jester.png              # 笑話師
│   ├── translator.png          # 翻譯官（Phase 2）
│   ├── reviewer.png            # 審核員
│   ├── scholar.png / merchant.png / farmer.png / chef.png  # 學者、商人、農夫、廚師：給新村民挑的外觀
│   ├── mayor.png               # 村長 = orchestrator（system，不給村民用）
│   ├── craftsman.png           # 工匠 = 村民工坊（system，不給村民用）
│   └── auditor.png             # 審核員 = 最終答案檢查（system，不給村民用）；reviewer.png 改名「巡查員」給村民用
├── deco/                       # 地圖裝飾：tree、pine、bush、flowers、rock、well、house_red、house_blue、sign、workbench
└── ui/status_bubbles.png       # 頭頂狀態泡泡
```

![preview](images/sprites_preview.png)

### 角色 sheet 規格（替換素材時必須遵守）

- 單格 **32×32 px**，透明背景，**3 欄 × 4 列 = 96×128 px**
- 列：`0=下(面向鏡頭)`、`1=左`、`2=右`、`3=上(背面)`
- 欄：`0=踏步A`、`1=站立`、`2=踏步B`；走路動畫播放 `0,1,2,1`
- 腳底約在 y=28，左右置中（站在格子中央）
- 新角色檔名 = agent 的 `id`，並在 `agent.yaml` 的 `x-village.sprite` 指定

### 狀態泡泡

16×16，第 0 列 working 動畫 4 格；第 1 列 `error / done / thinking / idle`。

---

## 用 AI 生成正式素材的 Prompt

> 實務提醒：圖像模型很難一次產出格線完全對齊的 sprite sheet。
> 建議流程：先生成「角色設定圖（四向站姿）」→ 用 Aseprite / Piskel / Libresprite
> 以 32×32 重描或修整、補走路格。也可試 PixelLab 這類專門做像素 sprite 的工具。

### 通用模板（英文效果較好）

```
Pixel art character sprite sheet for a top-down 2D RPG village game.
Character: {角色描述}.
Style: cute chibi proportions (big head, small body), 32x32 pixels per frame,
limited palette (max 16 colors), 1px dark outline, no anti-aliasing, flat shading,
similar to Stardew Valley / classic RPG Maker characters.
Layout: a grid of 3 columns x 4 rows on a transparent (or solid magenta #FF00FF) background.
Rows from top: facing down (front), facing left, facing right, facing up (back).
Columns: walk frame A, standing, walk frame B.
All frames same size, perfectly aligned to the grid, character centered, feet at the same baseline.
No text, no shadows outside the character, no background scenery.
```

### 各角色描述（填入 `{角色描述}`）

| id | 描述 |
|---|---|
| poet | a gentle young poet with brown hair, purple beret, green robe, holding a white feather quill |
| jester | a cheerful jester with orange hair, red-and-yellow two-pointed jester hat with bells, red and yellow split outfit |
| translator | a scholar with black hair, round glasses, blue coat, holding a brown book |
| mayor | an old village chief with white beard, small golden crown, dark red robe with gold trim, holding a wooden staff with a cyan gem |
| reviewer | a strict inspector in a navy blue uniform and cap with a gold badge, red armband |
| scholar | a scholar in a purple robe and black graduation cap with a gold tassel, holding a scroll |
| merchant | a merchant with a wide brown hat with a red band, teal coat, holding a coin pouch |
| farmer | a farmer in a wide straw hat and green overalls, holding a rice stalk |
| chef | a chef in a tall white toque and white apron, holding a ladle |
| craftsman | a craftsman in an orange hard hat, blue work clothes, tool belt, holding a hammer |
| auditor | a stern judge-like auditor in a black robe with a white collar and a square cap, holding a magnifying glass |
| calculator | a small merchant with an abacus, green apron, round cheeks (Phase 4) |
| chart | a cartographer with a rolled map and colored pencils, yellow scarf (Phase 4) |
| taiwan-history | an elderly storyteller in traditional Taiwanese clothing, holding an old scroll, wise expression (Phase 6) |

### 地圖 tileset

建議直接用 CC0 素材：Kenney「Tiny Town」或「RPG Urban/Roguelike」系列（16×16 tile，放大 2 倍與 32×32 角色搭配）。
若要自己生成：

```
Top-down pixel art tileset for a cozy village, 16x16 pixels per tile, grid aligned,
includes grass, dirt path, stone path, water edges, trees, flowers, fences,
small houses (roof and walls), a market stall, a well, a notice board.
Limited palette, no anti-aliasing, Stardew Valley style.
```

### 地圖裝飾

`deco/*.png` 由 `scripts/gen_placeholder_sprites.py` 產生，和角色一樣在地圖上放大 2 倍。擺放位置與擋路範圍寫在
`frontend/src/village/VillageScene.ts` 的 `DECOR` / `DECOR_BLOCK`（另有程式畫的池塘 `POND`）：

- 大型裝飾（房子、大部分的樹）放在第 0～2 列，村民不會走到那裡
- 擋路的格子村民閒晃時會避開（目的地與路線都檢查）；花可以踩過去
- 住處落在障礙物上會挪到最近的空地；住處小地圖會標出障礙物；工匠挑住處時也會避開（前端把障礙物傳給 `/api/workshop/generate`）
