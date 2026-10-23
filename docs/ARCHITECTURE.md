# Multi-Agent Village — 架構設計

> 一個像素風的虛擬村落。每個 agent 是村裡的一位村民，平常隨意閒晃；
> 當你在旁邊的問答框提問時，村長（orchestrator）指派村民合作完成任務，
> 工作中的村民頭頂會冒出「運作中」泡泡。

## 1. 全景

```
┌──────────────────────────── Browser ─────────────────────────────┐
│ 登入頁：示範村落（劇本驅動）＋ 登入 / 註冊                          │
│ 村子：Phaser 3 像素地圖滿版（村民閒晃、頭頂泡泡、交接動畫）          │
│   右上 ⚙️ → 右側抽屜：村民 / 紀錄 / 記憶 / 村規 / 帳號 / LLM 設定    │
│   右下 💬 → 問答視窗（對話列表、執行過程、停止、圖片放大）           │
└───────────────▲─────────────────────────────────────▲────────────┘
                │ WebSocket：事件流（只有自己的內容）    │ REST（cookie session）
┌───────────────┴─────────────────────────────────────┴────────────┐
│ Backend (Python / FastAPI)                                        │
│                                                                   │
│  帳號 / 權限 ─► 對話 + 長期記憶 ─► 村長（規劃，含 @指名 / 關鍵字退路）│
│                                     │ Plan（steps + use + prev）  │
│                                     ▼                             │
│  Executor：依相依關係分批（可平行）、逾時、取消、寫執行紀錄           │
│        ┌──────────────────┬──────────────────┬──────────────┐     │
│        │ 對話型（prompt）  │ 工具型（tool）     │ remote（Phase 6）│
│        │ 工作守則 + LLM    │ LLM 整理參數 → 程式 │ 獨立容器        │
│        └────────┬─────────┴────────┬─────────┴──────────────┘     │
│                 ▼                  ▼                              │
│   Harness（輸出檢查、重做）→ 審核員 → 回答；書記在背景記下長期記憶     │
│   LLMService：每個帳號自己的模型與 API key（加密），large / small 備援 │
│   系統角色：村長、工匠、審核員、書記                                  │
│   Store：SQLite（Alembic；可切 Postgres）                           │
└───────────────────────────────────────────────────────────────────┘
```

## 2. 技術選型（建議）

| 層 | 選擇 | 理由 |
|---|---|---|
| 後端 | Python 3.12 + FastAPI + uv | 之後的 RAG / 資料分析生態都在 Python |
| LLM 呼叫 | 自己寫薄薄一層 `LLMProvider`，底層用 **OpenAI 相容 API**；provider / 模型在 UI 註冊，存 DB | Ollama、vLLM、LM Studio、OpenAI、多數雲端都支援同一個介面；Anthropic 另寫一個 adapter |
| Agent 間協定 | 內部：Python 呼叫；外部：**A2A (Agent2Agent) 協定** 的 Agent Card + JSON-RPC | Agent Card 概念本來就是 A2A 的，照著做之後就能接別人的 agent |
| 前端 | Vite + TypeScript + React（聊天面板）+ **Phaser 3**（村落） | Phaser 對像素地圖、sprite 動畫、tilemap 支援成熟 |
| 地圖 | Tiled 編輯器 + Kenney「Tiny Town」等 CC0 tileset | 免費可商用，風格一致 |
| 即時通訊 | WebSocket | 推送 agent 狀態事件給村落動畫 |
| 儲存 | SQLAlchemy 2 + SQLite（MVP）→ Postgres/TimescaleDB | 用 ORM 隔開，換 DB 只改連線字串 + migration (Alembic) |

## 3. 核心概念

### 3.1 Agent Card（名片）

每位村民都有一張名片，同時給三種用途：**村長規劃時看**、**agent 之間互相認識**、**前端顯示**。
格式以 A2A AgentCard 為基礎，加上村落專屬欄位（`x-village`）。

```yaml
# agents/poet/agent.yaml
card:
  id: poet
  name: 詩人
  description: 根據主題寫一首短詩（中文為主，可指定語言與風格）
  version: 0.1.0
  skills:
    - id: write_poem
      description: 依主題寫詩
      input:  { topic: string, style?: string, lines?: int }
      output: { poem: string }
      examples: ["寫一首關於秋天的詩", "用李白風格寫月亮"]
  tags: [creative, writing, zh]
x-village:
  sprite: poet            # 對應 frontend/public/assets/sprites/characters/poet.png
  home: [12, 8]           # 地圖上的家（閒晃中心）
kind: prompt              # prompt | tool | remote
model:
  tier: small             # small | large → 對應 config 裡的實際模型
  temperature: 0.9
prompt:
  system: |
    你是村裡的詩人。只輸出詩本身，不要解釋。……
  user_template: |
    主題：{topic}
    風格：{style|自由}
validate:                 # harness 會用到的輸出檢查
  max_chars: 400
  must_not_be_empty: true
```

**設計重點：**
- `skills[].input/output` 是村長串接步驟的依據（上一步的 `poem` 能不能餵給下一步的 `text`）。
- `examples` 很重要：弱模型靠例子判斷要選誰。
- `kind: prompt` 的 agent **只需要這個 yaml**，不用寫程式 → 之後可以直接在 UI 上表單建立。

### 3.2 三種 Agent 類型

| kind | 怎麼建立 | 例子 | 執行位置 |
|---|---|---|---|
| `prompt` | 介面上填工作守則（System prompt） | 詩人、笑話師、英文翻譯 | 後端同程序 |
| `tool` | 介面上選「工具型」並挑工具箱裡的工具（`tool.id`），不用寫程式 | 計算機（安全求值）、圖表師（SVG）、報時員（日期） | 後端同程序 |
| `remote` | `agent.yaml` 只填 `endpoint`，實際程式在另一個容器 | 台灣史專家（RAG / GraphRAG / agentic RAG） | 獨立容器，走 A2A |

**工具型**（`core/executor.py` 的 `run_tool_agent`，工具在 `backend/village/tools/`）：LLM 依工具的 JSON schema 整理參數
（`prompt.system` 留空就用工具的 `default_prompt`）→ `tool.run(args)`；`ToolError` 會把原因回饋給 LLM 重來，
LLM 不能用或一直失敗時改用 `tool.fallback()` 從文字抓參數。工具可回傳附件（SVG），隨 `agent.finished` / `run.finished`
送到前端，也寫進執行紀錄。新增工具：在 `tools/` 寫一個 `Tool` 子類別並在 `tools/__init__.py` 註冊，介面上就能選。

所有類型都實作同一個介面（規劃中）：

```python
class BaseAgent(Protocol):
    card: AgentCard
    async def run(self, task: Task, ctx: RunContext) -> AgentResult: ...
# ctx 提供：llm、emit(event)、memory、user、取消訊號
```

村民定義存在 DB（`agents` 表，`spec` 欄位與 `agent.yaml` 同格式），在前端「村民」分頁新增 / 編輯 / 停用 / 刪除、試打、跑測試題、匯入匯出 YAML（API：`/api/villagers`）。
存檔後後端重新載入名冊並發 `agents.changed`，村子裡的角色即時登場 / 換裝 / 離開。
`agents/*/agent.yaml` 是內建範例：啟動時只匯入 DB 還沒有的 id；刪掉的內建範例留有紀錄，不會在重啟時回來，可用「還原內建範例」救回。

**村民工坊（工匠）**（`core/workshop.py`、API `/api/workshop`）：使用者跟工匠聊天，工匠產生村民設定草稿。
`mayor`、`craftsman` 是系統角色（保留名稱），不會被派去做問答任務。

- `POST /interview`：工匠回 `{reply, ready}`——資訊不夠就用日常問題追問，夠了就複述理解並請使用者按「打造」
- `POST /generate`：LLM 只填扁平的「藍圖」，程式組成 AgentSpec（ID、外觀、住處、模板、temperature / 字數換算、過濾測試題條件）；
  不合格帶錯誤重做，仍失敗則用對話內容拼基本草稿。帶 `draft` 時是在既有草稿上修改（id、住處、指定模型沿用）
- 草稿不會自動儲存，交給前端編輯器確認後走 `POST /api/villagers` 建立（修改既有村民則走 `PUT`）
- 字數上限（`validate.max_chars`）由程式寫成工作守則最後一條 `- 長度：…`，重新打造時替換，不會重複
- 可選外觀由前端從 `manifest.json` 傳入（`system: true` 的角色排除），`desc` 給工匠挑外觀參考

### 3.3 Orchestrator（村長）

村長本身也是一個 agent，但特殊：它的輸入是使用者訊息，輸出是 **Plan**。

```json
{
  "steps": [
    {"id": "s1", "agent": "poet",   "instruction": "寫一首關於貓的詩",   "use": []},
    {"id": "s2", "agent": "jester", "instruction": "把這首詩改編成笑話", "use": ["s1"]}
  ]
}
```

- 每一步只有「誰、一句指示、要用哪幾步的成果」——刻意不讓模型填結構化參數，弱模型也做得到
- Executor 把 `use` 指到的成果組成 `{context}`（「參考資料（來自詩人）：…」），連同 `{instruction}` 填進該 agent 的 `user_template`
- 最終答案 = 最後一步的輸出
- 這樣 **規劃（LLM 做）** 和 **執行（程式碼做）** 分開

規劃的退路順序（`backend/village/core/orchestrator.py`）：

1. `@指名`：`@poet 寫詩 → @jester`，完全不經 LLM
2. LLM 規劃：JSON schema 約束（`agent` 是 enum），解析或驗證失敗會把錯誤回饋給模型重試（最多 2 次）
3. 剪掉「沒被要求」的步驟：村民的 `x-village.keywords` 一個都沒出現在請求裡、且沒人依賴它 → 移除（弱模型常照抄範例多加步驟）
4. 關鍵字備援：LLM 不可用或一直規劃失敗時，依關鍵字在請求中出現的順序串接
5. 村長直接回覆：沒有村民適合（打招呼、閒聊）

### 3.4 為「弱模型」設計的保險

本地小模型（例如 7B~14B）在多步規劃上常出錯，以下機制讓它也能用：

1. **分級模型**：在介面的「LLM 設定」頁把已註冊的模型指派到 `large` / `small` 兩級（各自可排多個備援），例如 `large=雲端模型`, `small=ollama:qwen2.5:7b`。村長預設用 `large`，呼叫失敗時依序退到備援、最後退到 `small`。詳見 3.7。
2. **結構化輸出**：Plan 一律要求 JSON，並用 JSON Schema 約束（Ollama `format`、OpenAI `response_format` 都支援）。
3. **解析失敗 → 修復重試**：把錯誤訊息回貼給模型重試 1~2 次；仍失敗則退回下一層。
4. **退路 1：規則路由**：用關鍵字 / `examples` 的相似度直接挑單一 agent。
5. **退路 2：使用者指名**：支援 `@poet 寫一首詩 → @jester`，完全跳過 LLM 規劃。
6. **縮小選擇範圍**：agent 多了以後，先用 embedding 從所有 card 中挑 top-k 給村長看，避免 prompt 太長。
7. **Prompt 短而具體**：每個 agent 的 system prompt 只做一件事，並附 few-shot 例子。

### 3.5 Harness（村莊秩序）

把「驗證、錯誤回饋、審核」做成包在每次 agent 執行外面的一層，而不是寫進每個 agent。

```
使用者輸入 → [InputGuard] → 村長規劃 → 每一步：
    agent.run() → [OutputValidator] → 失敗? 帶錯誤訊息重試(最多N次) → 仍失敗? 標記 error 並回報
→ 最終答案 → [ContentGuard / 審核員] → 回覆使用者
```

| 元件 | 做什麼 | 實作 |
|---|---|---|
| InputGuard | 擋惡意 / 越權 / prompt injection 的明顯樣式 | 規則 + 可選的 LLM 分類 |
| OutputValidator | 依 card 的 `validate` 檢查：非空、長度、JSON schema | 純程式 |
| ContentGuard | 髒話、不當內容 | 先用詞表（中英）；之後加「審核員 agent」做 LLM 判斷 |
| Retry with feedback | 失敗時把「哪裡不對」回饋給 agent 重做 | Executor 內建 |
| Timeout / 取消 | 每步上限秒數，可從 UI 取消 | asyncio |
| Trace | 每次 run、每一步的輸入輸出、耗時、token | 存進 DB，前端可展開看 |

**審核員**（`core/reviewer.py`）是系統角色 `reviewer`，在村落裡也看得到它出來工作。流程：

1. 所有步驟完成後，把「使用者的請求」與最終步驟（沒有其他步驟用到它的成果，平行時可能有多個）的成果交給審核員
2. 結構化輸出 `{pass, reason, fix, step}`；只有明顯問題才判不合格，風格偏好不算
3. 不合格 → 把理由、修改建議與被退回的成果交給該步驟的村民重做（`review_redo` 次），再審一次
4. 審核員不能用或輸出無法解析 → 略過，不擋答案

**村規**（`store/settings.py`，`settings` 表，API `/api/rules`）：審核員開關 / 模型 / 重做次數、平行執行 / 上限、每步逾時、紀錄保留筆數。

**執行紀錄**（`store/runs.py`，`runs` 表，API `/api/runs`）：`POST /api/chat` 先建立紀錄再回傳 run_id，結束時補上計畫、
每一步（送出的完整訊息 `input`、輸出、模型、耗時、token、重做原因、被審核員退回的版本）、審核結果與總 token。
伺服器重啟時沒跑完的標成 `interrupted`；超過保留筆數的舊紀錄自動刪除。

**取消**：`POST /api/chat/{run_id}/cancel` 取消該問答的 asyncio task，正在等的 LLM 呼叫會中斷，發 `run.cancelled`。

**平行**：Executor 依 `use` 一批一批執行，同一批互不依賴，開啟平行時用 `asyncio.gather` 同時跑（Semaphore 限制數量）。

### 3.6 事件流（驅動村落動畫）

後端每個動作都發一個事件，前端據此動畫：

| 事件 | 前端效果 |
|---|---|
| `run.started` | 村長頭頂出現 💬 |
| `plan.note` | 規劃中的即時紀錄（例如「第 1 次規劃無效，請村長重新規劃」），顯示在執行過程 |
| `plan.created` | 村長走向第一位被指派者 |
| `agent.started` | 該角色停止閒晃、頭頂 working 泡泡動畫；`step_id` 為 null 代表村長直接回覆 |
| `agent.delta` | 串流中的半成品，聊天框即時顯示 |
| `agent.handoff` (from→to) | 角色走向下一位（帶著卷軸） |
| `agent.finished` / `agent.error` | 泡泡變 ✓ / ❗，幾秒後恢復閒晃 |
| `review.started` / `review.finished` | 審核員出動；finished 帶 `passed`、`reason`、`fix`、`step`（或 `skipped`） |
| `run.cancelled` | 使用者按停止，所有忙碌的村民收工 |
| `workshop.started` / `workshop.finished` / `workshop.error` | 工匠（`agent: craftsman`）追問或打造村民時的動態 |
| `memory.started` / `memory.finished` | 書記（`agent: scribe`）在問答後記錄；finished 帶 `added`、`forgot` |
| `agents.changed` | 村民名冊有變動，村子裡的角色登場 / 換裝 / 離開 |
| `appraise.started` / `appraise.finished` | 鑑定師（`agent: appraiser`）看圖；finished 帶 `ok`、`text`、`count`、`relook` |
| `residents.status` | 駐村專家在村 / 外出中改變（`agent`、`status`）；前端重新讀名冊，外出中的半透明並標註 |
| `loop.verdict` | 迴圈的審查結論（`step_id`、`passed`、`feedback`、`how`、`round`、`max_rounds`、`back_agent`、`retry`、`stop`）；不通過要重做時接著發 `agent.handoff`（審查者 → 被退回的村民） |
| `run.finished` | 聊天框出現最終答案 |

```json
{"type": "agent.started", "run_id": "r_123", "step_id": "s1", "agent": "poet", "ts": "..."}
```

**地圖互動**（只在前端，`village/VillageScene.ts`）：閒著的村民每 8～15 秒有人冒一句（台詞由 `App.tsx` 的 `idleLines`
從名片產生；系統角色的台詞寫在 `MAYOR` 等常數），同時最多兩個泡泡；每 30～60 秒兩位附近的村民湊在一起聊幾句；
收到 `agent.started` 等事件時立刻中斷去工作。點村民 → App 開新對話並在輸入框填「@id 」（村長不加 @；工匠、審核員、書記、
鑑定師只自我介紹；外出中的駐村專家說外出中）。

**串流**：`agent.delta` 帶的是「目前為止的完整文字」而不是增量，前端直接取代顯示，所以節流掉事件（每 80ms 最多一次）、
換備援模型或重做時從頭來過都不會錯亂。半成品已先清掉 `<think>`、遮蔽髒話；正式結果仍以通過 harness 檢查的
`agent.finished` / `run.finished` 為準。村長規劃（JSON）不串流。

### 3.7 LLM 註冊與 API Key 加密

**不使用設定檔管理模型**，一律在前端「LLM 設定」頁註冊，資料存在 DB，而且**屬於各個帳號**：每個人註冊自己的
供應商與 API key、排自己的 large / small 分級，問答時用提問者自己的設定與額度（見 3.8）。

#### 註冊流程（UI）

1. 新增 Provider：選類型（`openai_compat` / `anthropic` / `ollama`）、填 `base_url`、填 API key（本地 Ollama 可留空）
2. 按「測試連線」：後端用該設定呼叫 `/v1/models`（Ollama 用 `/api/tags`）列出可用模型
3. 勾選要註冊的模型，可設顯示名稱、是否支援 JSON schema 輸出、context 長度
4. 在「模型分級」把模型拖進 `large` / `small`，排出備援順序
5. 村民設定裡選 `small` / `large` 分級，也可以指定某個模型（只對指定的人有效，其他人改用分級）
6. 視覺模型：在模型設定按「看圖測驗」，通過後才能放進 `vision` 分級（鑑定師看圖專用）

#### 視覺模型（vision 分級）

- `ChatMessage.images`（`ImagePart`：mime + base64）。OpenAI 相容送多段 `content`（`image_url` data URL），
  Anthropic 送 `image` block；沒圖片的訊息維持純文字。
- `vision` 分級自成一級：`tier:vision` 只取 vision 分級裡**通過看圖測驗**的模型，不借用 large / small；
  large / small 也不會用到 vision 分級。沒有可用模型時錯誤訊息提醒去做測驗。
- 看圖測驗（`llm/vision_test.py`）：純 Python 畫 PNG（隨機四位數字＋紅藍綠紫其中一色的圖形），
  數字與顏色都答對才通過，記在 `llm_models.vision_verified_at`；沒通過就清掉（vision 分級會略過它）。
  原因：有些模型收到圖片不會報錯，而是忽略圖片亂答。
- 問答附圖：前端 `chat/prepareImage.ts` 縮圖（長邊 ≤ 1568px）並畫到 canvas 重新輸出 JPEG（EXIF / GPS 不會送出）；
  `POST /api/chat` 的 `images`（data URL）由 `api/images.py` 把關：看檔頭判斷格式（PNG / JPEG / WebP / GIF）、
  每張 ≤ 2MB、張數 ≤ 村規 `max_images`；沒有可用的視覺模型回 409。圖片存 `chat_images`（user_id、conversation_id 都 CASCADE），
  使用者訊息的 `meta.images` 記 id；`GET /api/images/{id}` 只有上傳的人拿得到（管理員也不行）。nginx 上限 12MB。
- 鑑定師（`core/appraiser.py`，系統角色 `appraiser`）：有圖時在村長規劃前先用 `tier:vision` 看圖，依問題寫下細節
  （文字 / 數字 / 表格逐字抄），結果放 `_APPRAISAL`（context variable）帶進村長規劃、村長直接回覆、村民參考資料、審核員。
  事件 `appraise.started` / `appraise.finished`，看完 `agent.handoff` 走去找村長；結果記在執行紀錄 `plan.appraisal`。
  追問（這輪沒附圖）時 `wants_relook` 判斷問題是否跟圖有關（位置、顏色、文字、它 / 那個…），是就重看 `context_turns` 輪內最近附的圖，
  並附上前一輪問答當背景。看不了圖時告訴村長「看不到，不要猜」；村長 LLM 不能用時直接交出鑑定內容。

#### 駐村專家（外接的獨立 agent）

- 在別處獨立開發、獨立部署的 agent（`extra-agents/`），村子用 A2A 呼叫。村民定義 `kind: remote`，
  `remote: {url, a2a_path, share_llm}`；`remote_agents` 表存加密的存取 token（AAD `remote_agent:{id}:token`）、
  原樣保存的 agent card、健康狀態（online / offline / unknown）。
- `core/remote.py`：讀 card（`{url}/.well-known/agent-card.json`）、健康檢查（`/health`，沒有就讀 card；回 `ok: false` 視為外出中）、
  card → 村民定義。呼叫網址用管理員填的網址加上 card 裡 `url` 的路徑（card 寫的主機從容器多半連不到）。
- `api/residents.py`（管理員）：preview、invite、update、refresh、check；`health_loop` 每分鐘檢查，狀態改變發 `residents.status`。
- 「允許把提問者的 LLM 設定交給它」（`share_llm`）預設關閉：打開才會把提問者的模型網址、模型、key 放進 X-LLM-* header
  （`LLMService.shareable`：large 分級第一個 OpenAI 相容模型；Anthropic 不相容）。
- 派工：Runner 只把「在村」的駐村專家放進村長名冊（`RemoteDirectory.online_ids`）；`run_remote_agent` 用 A2A
  送「指示＋前一步成果」：先試 `message/stream`（SSE，文字片段交給 DeltaEmitter，和村民一樣逐字顯示），
  對方回 -32601 就改 `message/send` 並記住（`_NO_STREAM`，重啟後重試）。回覆的 text 是成果、data 的 citations 整理成「資料來源」附在後面。
  呼叫需要的目錄與提問者放 context variable `REMOTE_ENV`；token、key 只在 header，執行紀錄只記訊息內容。
- 地圖：`/api/agents` 帶 status，外出中的駐村專家半透明並標「（外出中）」；`residents.status` 事件讓前端即時更新。

#### 迴圈：改到過關為止（`core/loop.py`）

- 計畫最多一個迴圈 `plan.loop = {check, back_to, max_rounds}`（LLM 計畫的 JSON 多一個 `loop` 欄位，沒有就 null）。
  審查步驟是普通步驟（任何村民或駐村專家；內建「審稿人 @editor」專門挑毛病）。
- 村長漏排時程式補上（`loops.from_message`）：請求有「不通過就…／改到…為止／最多 N 輪」而且計畫裡有審查步驟
  （審稿人或指示含檢查、審、校對…）→ 退回審查的前一步；提到補資料、重查 → 退回第一步。@指名、關鍵字備援也適用。
- 執行（`Runner._execute` → `_judge_round`）：審查步驟的指示補上「最後一行寫『結論：通過 / 不通過』」；
  判定順序：讀結論 → 小模型判讀（`tier:small`，JSON）→ 都不行當作通過並註明。不通過且還有輪數 →
  `back_to` 到 `check` 的步驟重設成下一輪（舊成果收進 `StepResult.history`），重做時 context 帶「審查意見＋你上一版的成果」
  （context variable `_LOOP` 的 `LoopState.note_for`）。
- 上限：村規 `loop_max_rounds`（預設 3、最多 5；計畫寫的不能超過）、每次問答總步數 `MAX_TOTAL_STEPS = 15`。
  用完還沒通過 → 交出最後一版，回答後面註明最後的審查意見（`_loop_footnote`）。
- 回答是被審查的成果：`leaf_steps` 把審查步驟換成它用到的步驟（前端 `leafIds` 一樣）；審查意見只在執行過程。
- 審查者和被審查的是同一位村民（自己審自己）→ 不排迴圈（`loops.validate`）。
- 審查步驟之後又排了「被退回的村民依意見修改」→ 多餘（迴圈會處理），規劃後移除（`drop_redo_steps`）。
- 停用中的村民：`registry.disabled`（id →「名字（說明）」）放進村長的規劃提示（不能派工、不要找別人代替）與直接回覆
  （請使用者到村民頁啟用）。

#### A2A：村子和駐村專家怎麼溝通

- **中樞式**：專家之間不直接對話，也不知道彼此存在。多位專家合作（研究員 → 審稿人 → 退回研究員）是村長排計畫、
  執行器把上一步的成果（＋審查意見、附的文件）放進下一步的訊息轉交。
- **Agent Card**（`GET /.well-known/agent-card.json`，公開）：`name` / `description` / `skills`（含 examples）→ 村民定義，
  村長靠這些文字決定派工；`url` 只取路徑（主機用管理員填的網址）；`capabilities.streaming` → 先用 `message/stream`；
  `securitySchemes` bearer → 存取 token；`inputModes` / `x-file-limits` → 收哪些文件；`x-llm-headers`（自訂）→ 需要呼叫端提供 LLM。
- **呼叫**（`POST {網址}{路徑}`，JSON-RPC 2.0）：header 帶 Bearer token 與（打開共享時）X-LLM-*；訊息 parts＝text（指示＋參考資料）
  ＋ file（base64）。SSE 回覆：task → status-update（進度，⏳ 顯示）→ artifact-update（text append、file、data）→ final。
  data 的 `citations`（n、title、section、url）三位專家格式相同，村子統一整理成「資料來源」。
- **無狀態**：每次呼叫獨立（不用 contextId 延續）；追問 / 迴圈重做時村子重新帶上下文與文件。
- 還沒用到：`input-required`（專家反問使用者）、`tasks/get` 與推播（長任務，語音轉文字會需要）。

#### 附文件（交給收文件的駐村專家）

- 村子自己不讀文件內容。名片寫著收哪些檔案的駐村專家（`x-file-limits.extensions`，或 inputModes 的 mime；
  `text/plain` 不算）→ `remote.accepts`（`core/docfiles.accepts_from_card`，重新讀取名片時更新）。
- 💬 📎 附 xlsx / csv / txt / pdf（最多 3 份、每份 10MB、合計 20MB；`core/docfiles.py` 看副檔名＋檔頭）。
  沒有「在村」且收這類檔案的駐村專家 → 409 / 422。文件存 `chat_files`（`kind=upload`，跟著對話保存），
  使用者訊息 `meta.files = [{id, name, size}]`。
- Runner 拿到 `docs`（這次附的，或前幾輪附的）放進 `REMOTE_ENV.docs`；村長名冊標「收文件」、規劃提示列出檔名。
  村長漏派時補救：這次附了文件、或追問上一份報告（`_DOC_FOLLOWUP` / 修改字眼，且上一輪是收文件的專家做的）→ 直接派給它。
- `remote.call` 把文件放進 A2A 訊息的 file part（base64）；對方的 `status-update` 進度（「讀取檔案 2/3」）用 `⏳` 先顯示；
  回傳的 file part（報告 PDF）存 `chat_files`（`kind=report`，14 天過期，`purge_expired` 一起清）→ 附件
  `{kind: "file", url: "/api/files/{id}"}`。收文件的步驟時間上限至少 900 秒（`DOC_STEP_TIMEOUT_S`）。
- `GET /api/files/{id}`：只有本人拿得到，一律 `Content-Disposition: attachment`；過期回 404（前端顯示「已過期」）。

#### 生圖模型（image 分級）

- OpenRouter 的生圖 API：`POST {base_url}/images`（`model`、`prompt`、`n`、`aspect_ratio`）→ `data[].b64_json`、`media_type`、
  `usage.cost`；列出模型 `GET {base_url}/images/models`。`LLMClient.generate_image` / `list_image_models`（其他 client 預設不支援）。
- 生圖模型註冊時標記 `llm_models.output="image"`：只能放 `image` 分級；large / small / vision 會排除它，`chat()` 也拒絕。
- 生圖測驗（`POST /api/llm/models/{id}/image-test`）：畫一顆紅蘋果，拿到 PNG / JPEG / WebP / GIF（看檔頭）就通過，
  記在 `image_verified_at`；有視覺模型時請它看一眼（只供參考）。SVG 不收（從我們的網址開啟可能執行程式碼）。
- 畫作存 `chat_images`（`kind=generated`、`expires_at` = 14 天後；生圖測驗產生的沒有 conversation_id），
  讀取時過期就當不存在，啟動時與每 6 小時清理一次。
- 畫家（內建村民 `painter`，工具型，工具 `tools/painter.py`）：LLM（small 分級）把請求和參考資料整理成
  `{prompt（英文）, aspect_ratio, title}` → `core/painter.py` 的 `PaintEnv.paint` 檢查村規上限（`paint_per_run`、
  最近 24 小時 `paint_per_day`）→ `tier:image` 生圖 → 只收點陣圖 → 存成畫作 → 附件 `{kind: "image", url: "/api/images/{id}"}`。
  `PaintEnv` 放 context variable `PAINT_ENV`（api/chat.py、api/villagers.py 試打各自設定）。LLM 不能用時用請求原話畫。
  工具 `costly = True`：審核員退件時不重畫，避免重複計費；村長名冊會註明「每次都要花錢」，
  `prune_unrequested` 也會移除重複、而且不是請求原話的畫家步驟。
- 修圖：`PaintEnv.references`（`Reference`：mime、data、來源 upload / painting）由 api/chat.py `_references` 決定——
  這次附的圖 → 追問像在修改時用上一輪的畫作（`ConversationStore.last_painting`）→ 鑑定師重看的前幾輪附圖。
  工具 `context()` 告訴畫家有沒有參考圖與寬高（`imagefmt.dimensions`），畫家用 `use_reference` 決定是否以它為底；
  送出時 `input_references` = data URL。模型回 400（不收參考圖）時改成只依描述重畫並在結果註明。

#### 介面風格（像素 / 柔和）

`styles.css` 是像素風格（深藍灰、黃色、粗黑框、直角、錯開的實心陰影），顏色幾乎都走主題變數。
`theme-soft.css` 只在 `<html data-theme="soft">` 時生效：換變數（米白面板、珊瑚橘），再覆蓋粗框、直角、實心陰影、深色底。
選擇器的根用 `:where(:root[data-theme="soft"])`（不加權重），元件自己更具體的樣式（例如島嶼工具列）仍然優先。
App 依帳號選單的「介面風格」（localStorage `ui-theme`：柔和（預設）/ 像素）設定 `data-theme`。
登出（登入頁）時移除，登入頁一律像素。新增樣式時：先寫像素版，柔和版需要不同外觀再到 `theme-soft.css` 補。

#### 島嶼版地圖（預設地圖；像素村只剩備案）

地圖和其他部分只靠一個小介面溝通（`VillageHandle`：`dispatch(事件)`、`say(id, 話)`；props：村民名單、點了誰），
所以另外寫了 3D 版 `island/IslandCanvas.tsx`，介面一模一樣。
3D 的程式分兩層（R0 重構，為了鐵道小鎮）：`world3d/` 是場景共用的部分——`World3D`（鏡頭、日夜、村民名單、事件、協作線、
閒聊、生活作息的佔位與繞路、點村民）、`Villager3D`（一位村民：走路、派工、交接、姿勢與道具）、`WorldCanvas` 與 `Hud`
（工具列、村莊日誌）、`town.ts`（村子本身：十字路、裝飾、池塘、路燈、生活作息的地點）、`models.ts`；場景只要實作
`common.ts` 的 `Terrain`（地形、範圍 `inside`、繞路的路口、駐村專家住的地方 `outpost` → `Outpost{center, radius, gates, door, desk}`、
依時間換顏色 `tint`、每幀 `update`、鏡頭設定 `view`）。島嶼是 `island/IslandTerrain.ts`（海、六邊形主島、外圍小島與木棧橋、船、雲）。使用者決定只留島嶼（2026-10-23）：登入後一律島嶼，
沒有切換按鈕；瀏覽器畫不了 3D（`hasWebGL()` 失敗）時自動用像素村，網址 `?view=pixel` 可強制看像素村（測試用）。
像素版程式還在（登入頁示範村落也還用它），等登入頁改成島嶼版、使用者用一陣子沒問題後再刪（含 Phaser）。

- Three.js（`React.lazy`）切到島嶼才下載（壓縮後約 157KB）；不用模型檔或貼圖，全部用方塊、圓柱、圓球組成（`world3d/models.ts`）。
- 主島：圓角六邊形；像素地圖的 30×20 格對應到島上（`toWorld`，一格 0.9 單位），裝飾、池塘、小路的位置和像素版一樣。
- 駐村專家住在外圍小島，用木棧橋連到主島（`isletFor`）；交接時沿橋走（`walkTo` 自動加上兩端的橋頭）。
- Q 版村民的造型依像素外觀（sprite id）：衣服顏色同 `gen_placeholder_sprites.py`，再加配件（王冠、貝雷帽、耳機…）。
- 名牌、冒泡、狀態用 HTML（CSS2DRenderer，`.isl-*` 樣式）；村民行為（冒一句、聊天、派工、交接、外出中）照像素版。
- 生活作息（使用者提議「看起來像在過生活，需要時才停下來」）：「做什麼」在 `village/life.ts`（不依賴畫法，像素版之後共用）——
  活動表 `ACTIVITIES`（圖示、地點種類、姿勢、道具、時間、能不能邊做邊聊、台詞）、`rolesOf`（名字 / 技能的關鍵字優先，
  再看外觀：畫家寫生、工匠敲打、計算機打算盤、報社看報、網路研究員拿望遠鏡…）、`periodOf` / `nextActivity`
  （平常約六成做拿手的事、兩成五散步、一成五釣魚 / 澆花 / 打水這類共同的事；清晨伸懶腰、中午吃飯、傍晚廣場喝茶各一半；
  22:00～5:30 回家睡覺）。「在哪裡、怎麼做」在 `world3d/`：`townSpots` 依裝飾算出地點（井邊、池塘邊、樹下、告示牌前、
  花圃、工作台、廣場、房子門口，各有位子上限，`claimSpot` / `releaseSpot`），`route` 直走會撞到東西就繞十字路口；
  駐村專家只在自己小島上（`Islet.desk` / `door`）。村民模型的手腳掛在肩 / 髖關節（`Chibi.arms` / `legs` 是 Group），
  `animatePose` 每幀擺姿勢（坐下、釣魚、寫字、敲打、伸懶腰、打瞌睡…），道具 `models.prop`。
  派工、交接、被找去聊天時 `interrupt`：收起道具、頭上閃 ❗（睡著的會說「來了來了！」），做完回去接著做（`resume`；
  天亮了不回去睡）。在廣場喝茶這類 `social` 的事可以邊做邊聊；換時段時（工具列）大家馬上照新的作息。工具列「🏡 生活」可關掉（回到隨便走走）。
- 日夜：`DAY` 關鍵影格（天空、太陽 / 月光、天地光、海色、夜晚程度）依真實時間每 20 秒內插；夜晚窗戶與路燈的發光材質
  （材質名 `window` / `lamp`）和路燈點光源一起亮、星星浮現。網址 `?time=night`（或 `dawn`、`day`、`dusk`、`HH:MM`）可預覽。
- 搬運圖示：`agent.finished` 有附件就照附件（🖼️ 圖、📊 圖表、📄 PDF、📝 檔案），否則照外觀（`CARRY_BY_SPRITE`），
  駐村專家是 📦；別人的問答事件只有動畫欄位（`for_viewer`），一律照外觀。交接時頭上拿著，到了換到對方手上。
- 協作線：`agent.handoff` 時在兩人之間畫弧線（TubeGeometry）＋流動光點；那次問答結束 5 秒後淡出（別人的問答沒有 run_id：20 秒）。
- 介面層（`world3d/Hud.tsx`）：膠囊工具列（時段、名牌、搬運、協作線、生活、暫停、鳥瞰、日誌；`island-prefs`，網址 `?time=` 優先）
  與「村莊日誌 LIVE」（`describe` 把事件寫成一句話，最多 120 筆；點一下 `World3D.focus` 鏡頭飛過去）。
  名牌 / 搬運用外層 class 隱藏（`.hide-names`、`.hide-carry`）；暫停只停動畫，鏡頭照樣能轉。
- 拖曳旋轉、滾輪縮放（OrbitControls，限制角度與範圍）；點村民用 raycast（拖曳超過 5px 不算點擊）。開發模式 `window.__island`。

#### 中轉站：步驟之間交接檔案

像店家把外送餐點放在取餐區、貼上號碼：每一步產生的檔案放進這次問答的中轉站（`core/depot.py`，context variable `DEPOT`），
編上取件號碼（#1、#2…）。內容留在 `chat_files`，中轉站只記號碼、檔名、誰放的、哪一步、檔案 id。

- 放件：步驟完成後，它的附件（`kind=file`，`/api/files/{id}`）由 `Depot.drop_attachments` 登記。
- 取件：下一步 `use` 了那一步、而且是收文件的駐村專家（`remote.accepts`）時，`Runner._parcels_for` 挑出它收的格式；
  那一步只有文字成果（摘要師、網路研究員）而對方收 txt 時，`drop_text` 把文字包成「{村民}的成果.txt」。
  出發前 `Depot.pick_up` 讀出內容（`load_file`：只讀得到提問者自己的檔案）→ `remote.handing` → `files_for`
  （使用者附的文件優先，合計最多 3 份）→ A2A file part。訊息裡附上取件通知（`slip`：「中轉站有…#1 逐字稿.txt（來自聽打員）」），
  已經用檔案交付的步驟成果不再重複貼進文字訊息（`render_context(skip=…)`；文件彙整師的「要求」上限 2000 字）。
- 駐村專家在別的容器、連不回村子，所以由村子代為取件、隨訊息送出（A2A 也支援 file part 只給 `uri`，
  以後檔案大到不適合隨訊息送、或專家能安全地回來拿時，可以改成給一次性的取件網址）。
- `StepResult.received`、`agent.started` 的 `received`：執行過程與紀錄顯示「📦 從中轉站取件：#1 …」。
- 回答附上每一步產生的檔案（最後交給使用者的步驟排前面），所以中間的逐字稿也下載得到。
- 文字包成的 txt 檔名帶上那一步的指示（「網路研究員_查最近的AI新聞.txt」），好幾位研究員的成果分得出來。
- 每位駐村專家一次收幾份看名片的 `x-file-limits.maxFiles`（`remote.max_files`，最多 10；報社收 6 份）。
- 駐村專家回傳的點陣圖（報社的第一版預覽）存成 `chat_images`（`kind=report`，14 天、不算畫家的張數），直接顯示在回答裡。
- 附了錄音時 `with_typist` 也補齊漏排的後續：請求要報告 / PDF 卻沒有收文件的專家 → 接上它；只有聽打員卻要摘要 → 接上摘要師。

#### 儲存空間

中轉站本身只在一次問答的記憶體裡，不存東西；會累積的是 DB 裡的檔案（`store/storage.py`）：
畫作、報告與逐字稿 14 天，錄音轉完就刪（最多 1 天），使用者附的圖與文件依村規 `attachment_keep_days`（預設 90 天，0 = 永久）。
每 6 小時（與啟動時）清理一次；SQLite 刪資料後檔案不會變小，空出來的空間超過 32MB 且超過檔案 1/4 時自動 `VACUUM`。
村規頁「🗄️ 儲存空間」顯示各類用量（`GET /api/rules/storage`），「立即清理」（`POST /api/rules/storage/cleanup`）會強制壓縮。

#### 語音轉文字模型（audio 分級）與聽打員

- `LLMClient.transcribe` / `list_audio_models`（只有 OpenAI 相容 client 實作）：`POST {base_url}/audio/transcriptions`（multipart）。
  要時間戳用 `verbose_json`＋`timestamp_granularities[]=segment`（whisper-1、Groq whisper）；gpt-4o 系列直接要 `json`，
  其他模型回 400 時也改要 `json` 並記在 `_QUIRKS`。語音模型從一般模型清單依名字挑（whisper / transcribe…）。
- 註冊時標記 `llm_models.output="audio"`：只能放 `audio` 分級（`LLMService.transcribe` 依序備援），不能對話。
  轉寫測驗（`POST /api/llm/models/{id}/audio-test`）：送 `llm/assets/audio_test.mp3`（macOS 台灣語音念的一句話），
  統一繁體、去標點、數字換國字後，相鄰兩字的吻合 ≥ 60% 才通過（`audio_verified_at`）。
- 錄音上傳：`POST /api/files/audio?name=…`（body 是檔案原始內容，邊收邊數大小，每份 200MB；看檔頭）→ `chat_files`
  （`kind=audio`、沒有對話、1 天後過期）；`GET /api/files/audio/ready` 先問能不能轉寫（村規、聽打員有沒有啟用、有沒有 audio 模型）。
  問答帶 `audio_ids` → `start_turn` 接到對話（`meta.files` 標 `audio: true`，追問文件時不會被當成文件）。
  nginx 對這個路徑另外放寬 `client_max_body_size 210m`、不暫存 body。
- 聽打員（內建村民 `typist`，工具型，工具 `tools/typist.py`）：LLM 只整理 `{language, terms}`；
  `core/transcriber.py` 的 `TranscribeEnv`（context variable `TRANSCRIBE_ENV`，api/chat.py 設定）→ `core/audio.py`：
  ffprobe 長度（村規 `transcribe_max_minutes`）→ ffmpeg 轉 16kHz 單聲道 48kbps mp3、每 600 秒一段（`-f segment`，csv 記起訖）
  → 同時 3 段送 `tier:audio`（提示「以下是繁體中文的逐字稿。可能出現的名詞：…」）→ 時間加上段落起點 → OpenCC `s2twp`（有假名不轉）
  → 約一分鐘一段的「〔mm:ss〕…」→ 存 `{檔名}_逐字稿.txt`、`{檔名}_字幕.srt`（`kind=report`，14 天）→ 刪錄音。
  執行過程顯示的模型是轉寫模型（`ToolResult.model`），不是整理參數的 LLM。
  進度用 `tools.base.report`（`PROGRESS` context variable，`run_tool_agent` 設定）顯示「⏳ 轉寫：2 / 6 段…」；
  工具 `timeout_s = 1800` 放寬步驟逾時；`costly = True`；同一次問答重做時用快取不再轉一次。
- 附了錄音：村長規劃提示加上「錄音只有聽打員聽得到，第一步一定是它」；村長漏派時 `with_typist` 在最前面補上
  聽打員步驟，原本沒有依賴的步驟改成用它的逐字稿。村長什麼都沒排、請求卻還要摘要 / 重點 / 待辦（`_WANTS_SUMMARY`）時，
  接著排內建村民「摘要師 @summarizer」（prompt 型、large 分級）。逐字稿中文旁的半形標點改全形（`to_traditional`）。

#### 資料表

| 表 | 主要欄位 |
|---|---|
| `llm_providers` | id, **user_id**, name（同一人不可重複）, kind, base_url, `api_key_ct`, `api_key_nonce`, `key_version`, `api_key_hint`(如 `sk-…a1b2`), enabled, created_at, updated_at |
| `llm_models` | id, provider_id, model, display_name, supports_json_schema, context_window, reasoning_effort, vision_verified_at, output(`text`/`image`/`audio`), image_verified_at, audio_verified_at, enabled |
| `model_tiers` | **user_id**, tier(`large`/`small`/`vision`/`image`/`audio`), position, model_id |
| `remote_agents` | agent_id（agents.id）, `token_ct`, `token_nonce`, `key_version`, `token_hint`, card, card_fetched_at, status, checked_at, last_error |

#### 加密設計（信封加密的簡化版）

```
主金鑰 (KEK, 32 bytes)  ──不進 DB、不進 git──
   │  AES-256-GCM(nonce=隨機 12 bytes, AAD="llm_provider:{user_id}:{id}:api_key")
   ▼
api_key_ct + api_key_nonce  ──存 SQLite/Postgres──
```

- **演算法**：AES-256-GCM（`cryptography` 套件），每次加密用新的隨機 nonce；帶 AAD 把密文綁定到該筆資料，複製到別的列會解不開
- **主金鑰來源**（依優先順序）：
  1. 環境變數 `VILLAGE_MASTER_KEY`（base64）——上線時由 Docker secret / 雲端 Secret Manager 注入
  2. 金鑰檔 `~/.config/multi-agent-village/master.key`（權限 600，**放在專案資料夾外**）；開發環境第一次啟動自動產生
- **只寫不讀**：API 永遠不回傳明文 key，只回 `api_key_hint`；UI 上是「已設定 / 重新輸入」。更新時沒填 key 就保留原值
- **用時才解密**：只在發出 LLM 請求的當下解密，存在記憶體；log、trace、錯誤訊息都經過遮罩（redaction），不會印出 key
- **輪替**：`village rotate-key`，用新主金鑰版本重新加密所有列，`key_version` +1
- **存取控制**：每個人只看得到、改得到自己的 provider / 模型（別人的 id 一律 404）；密文的 AAD 綁定擁有者，搬到別人名下解不開
- **git 防線**：`.gitignore` 排除 `*.db`、`data/`、`.env`、`*.key`；另可加 pre-commit 的 secret 掃描（gitleaks）

#### 這個設計擋得住什麼

| 情境 | 結果 |
|---|---|
| 專案推到 GitHub | 沒有任何 key 或 DB 進 git |
| DB 檔或備份外流 | 只有密文，沒有主金鑰解不開 |
| 有人連到 API | 拿不到明文，只看得到 hint |
| 攻擊者同時拿到 DB **和** 主金鑰（整台主機被控制） | 擋不住——這是所有應用層加密的極限，要靠主機安全與把主金鑰交給 Secret Manager / KMS 管理來降低風險 |

### 3.8 帳號、記憶與資料庫

**帳號（已完成，`security/auth.py`、`api/auth.py`）**：`users`、`auth_sessions`。第一位註冊者是管理員；
session token 放 httpOnly + SameSite=Lax cookie，DB 只存 SHA-256；密碼 scrypt；登入失敗 5 次鎖 60 秒。
`runs.user_id` 記錄提問者；EventBus 以 `claim(run_id, user_id)` 標記事件擁有者，WebSocket 依 `for_viewer` 對其他人只送動畫欄位。

**LLM 設定屬於各帳號（已完成）**：`llm_providers.user_id`、`model_tiers.user_id`（帳號刪除時 CASCADE）。
`LLMService.chat` 用 context variable `LLM_USER` 決定是誰的模型：Runner 在問答開始時以 `llm_user(user_id)` 設定，
`asyncio.gather` 會複製 context，平行步驟也沿用。村民「指定模型」是某個人的模型 id，別人用不到時以 `fallback`（村民設定的分級）代替。

**資料庫遷移（已完成，`store/migrate.py`、`store/migrations/`）**：Alembic，由程式呼叫（不需 alembic.ini），啟動時 upgrade head。

**對話與多輪上下文（已完成，`store/conversations.py`、`core/context.py`、`api/conversations.py`）**：
`POST /api/chat` 帶 `conversation_id`（沒帶就開新對話），先存使用者訊息、讀最近 `context_turns` 輪當 history，
問答結束後把回答（含附件、審核結果）存成 assistant 訊息。history 交給村長（system prompt 的「最近的對話」與 `prev` 規則）、
村長直接回覆（當成真的對話）、審核員（上一輪）；Executor 把 `use: ["prev"]` 換成「參考資料（上一輪的回答）」。
上一輪的回答放 context variable（Runner 全村共用，同時有多人問答）。

**長期記憶（已完成，`core/scribe.py`、`store/memories.py`、`api/memories.py`）**：問答結束且成功後，
`api/chat.py` 的 `take_notes_after` 請書記（`scribe`）看使用者這一句（附上既有記憶的編號清單），輸出 `{add, forget}`；
發 `memory.started` / `memory.finished`（含 added / forgot）事件。問答開始前 `MemoryStore.relevant` 挑出相關記憶，
以 context variable 帶進每一步的參考資料與村長直接回覆。

**以後（規劃中）**：

- 切 Postgres（已加 asyncpg，只改 `DATABASE_URL`）；記憶多了改用 pgvector 向量檢索
- `runs` 執行軌跡日後適合放 TimescaleDB 的 hypertable
- 全局預設的本地 LLM，給還沒設定自己 LLM 的人使用

## 4. 目錄結構

```
multi-agent-village/
├── backend/
│   ├── pyproject.toml / uv.lock
│   ├── tests/                   # pytest（uv run pytest）
│   └── village/
│       ├── main.py              # FastAPI 入口、AppContext、/ws、啟動與關閉
│       ├── cli.py               # village serve / rotate-key / reset-password / create-admin
│       ├── config.py            # 環境變數（DATABASE_URL、config_dir、agents_dir）
│       ├── events.py            # Event bus；claim / for_viewer（事件只給提問者）
│       ├── api/                 # auth、chat、conversations、memories、runs（含村規）、villagers、workshop、llm、
│       │                        # images（附圖 / 畫作）、files（附文件 / 報告）、residents（駐村專家）
│       ├── core/
│       │   ├── card.py          # 村民定義（AgentSpec：prompt / tool / remote）
│       │   ├── registry.py      # 記憶體中的村民名冊；保留名稱
│       │   ├── orchestrator.py  # 村長：@指名 → LLM 規劃 → 關鍵字備援 → 直接回覆
│       │   ├── executor.py      # 執行、平行、逾時、取消、審核迴圈、工具型村民、事件、紀錄
│       │   ├── harness.py       # 輸出清理與檢查、髒話、測試題條件
│       │   ├── reviewer.py      # 審核員
│       │   ├── workshop.py      # 工匠：對話打造村民
│       │   ├── scribe.py        # 書記：長期記憶
│       │   ├── appraiser.py     # 鑑定師：看圖（vision 分級）
│       │   ├── painter.py       # 畫家的作畫環境：生圖、張數上限、畫作保存、參考圖
│       │   ├── remote.py        # 駐村專家：agent card、健康檢查、A2A 呼叫（send / stream、文件與回傳檔案）
│       │   ├── docfiles.py      # 附文件：格式與大小檢查、哪些駐村專家收哪些檔案
│       │   ├── depot.py         # 中轉站：步驟產生的檔案登記取件號碼，後面收文件的駐村專家要用時取件
│       │   ├── audio.py         # 錄音：格式、ffmpeg 切段、逐字稿排版（時間、SRT）、簡轉繁
│       │   ├── transcriber.py   # 聽打員的轉寫環境：切段同時轉寫、存逐字稿、刪錄音
│       │   ├── loop.py          # 迴圈：審查結論判讀、從請求補迴圈、輪次狀態
│       │   ├── imagefmt.py      # 圖片格式判斷、寬高
│       │   └── context.py       # 多輪上下文（Turn、prev、指稱判斷）
│       ├── tools/               # 工具箱：calculator、chart（SVG）、clock、painter（生圖）、typist（聽打）
│       ├── llm/                 # openai_compat、anthropic_client、service（每個帳號的模型 + 備援）、vision_test、audio_test（＋assets/）
│       ├── security/            # crypto（AES-256-GCM、主金鑰）、auth（帳號 / session / 權限）、redact
│       └── store/               # models、migrations（Alembic）、agents、runs、settings（村規）、conversations、memories
├── agents/                      # 內建範例村民（啟動時補進 DB 沒有的）：poet、jester、calculator、chart、painter、editor、typist、summarizer
├── extra-agents/                # 獨立開發、獨立部署的外接 agent（村子裡叫駐村專家）
│   ├── taiwan-history/          # 台灣史專家：corpus（維基 175 篇）、app（RAG、A2A、測試網頁）、自己的 docker-compose（:8100）
│   ├── report-maker/            # 文件彙整師：讀檔與統計、報告 JSON、圖表、WeasyPrint PDF、範例檔 samples/（:8101）
│   ├── web-researcher/          # 網路研究員：自架 SearXNG（同一個 compose）、安全讀網頁、附引用回答（:8102）
│   └── newspaper/               # 報社：採訪資料 → 兩階段寫稿（記者 / 總編輯）→ A4 報紙 PDF＋第一版預覽 PNG（:8103）
├── frontend/
│   ├── public/assets/sprites/   # 角色四向圖、裝飾（deco/）、UI 泡泡、manifest.json
│   └── src/
│       ├── App.tsx              # 登入閘門、滿版村子、⚙️ 選單、抽屜、💬 問答視窗
│       ├── auth/                # 登入頁（示範村落劇本 demo.ts）
│       ├── village/             # Phaser 場景（地圖、裝飾、障礙物、系統角色、冒泡與村民互動、點村民）；life.ts 生活作息（兩版共用）
│       ├── world3d/             # 3D 地圖共用（Three.js，lazy 載入）：World3D（鏡頭、日夜、事件、協作線、生活作息）、Villager3D、
│       │                        # WorldCanvas＋Hud（工具列、日誌）、town（村子與作息地點）、models（低多邊形模型、Q 版村民、小道具）、common（Terrain 介面）
│       ├── island/              # 島嶼場景：IslandTerrain（海、主島、外圍小島與木棧橋）、IslandCanvas（介面同 VillageCanvas）
│       ├── chat/                # 問答面板、事件 reducer、附圖前處理、附文件（docs.ts）、@ 自動完成（mention.ts）
│       ├── villagers/           # 村民管理、編輯器、試打 / 測試題、工坊、駐村專家（Residents）
│       ├── traces/ memory/ rules/ accounts/ settings/   # 紀錄、記憶、村規、帳號、LLM 設定
│       └── ui/                  # Modal、Attachments（圖片放大、報告下載、文件標籤）、Markdown（marked＋DOMPurify）
├── samples/                     # 測試用的示範會議錄音（make_meeting_audio.py 用 macOS say 產生）
├── scripts/gen_placeholder_sprites.py
├── build.sh                     # 只 build image；部署由使用者執行 docker-compose up -d
├── docker-compose.yaml / .env.example
└── docs/                        # ARCHITECTURE、ROADMAP、SPRITES
```
