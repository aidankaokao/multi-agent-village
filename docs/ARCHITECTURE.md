# Multi-Agent Village — 架構設計

> 一個像素風的虛擬村落。每個 agent 是村裡的一位村民，平常隨意閒晃；
> 當你在旁邊的問答框提問時，村長（orchestrator）指派村民合作完成任務，
> 工作中的村民頭頂會冒出「運作中」泡泡。

## 1. 全景

```
┌──────────────────────────── Browser ─────────────────────────────┐
│  Village View (Phaser 3, 像素地圖+角色)   │  Chat Panel (React)   │
│  - 角色閒晃 / 頭頂狀態泡泡 / 交接動畫      │  - 問答、執行軌跡      │
└───────────────▲──────────────────────────┴──────────▲────────────┘
                │ WebSocket: 事件流 (agent.started …)  │ POST /chat
┌───────────────┴──────────────────────────────────────┴────────────┐
│ Backend (Python / FastAPI)                                        │
│                                                                   │
│  Gateway ──► Harness(輸入守門) ──► Orchestrator(村長)              │
│                                     │ 1. 讀 Agent Cards           │
│                                     │ 2. 產生 Plan (DAG/JSON)     │
│                                     │ 3. 依序/平行執行步驟          │
│                                     ▼                             │
│                         Agent Registry（市集）                     │
│        ┌──────────────┬──────────────┬──────────────────┐         │
│        │ PromptAgent  │ ToolAgent    │ RemoteAgent      │         │
│        │ (yaml+prompt)│ (python 類別) │ (A2A over HTTP)  │         │
│        └──────┬───────┴──────┬───────┴────────┬─────────┘         │
│               ▼              ▼                │                   │
│          LLM Provider 抽象層 (cloud / local)   │                   │
│                                                │                   │
│  Harness(輸出守門+審核員) ◄── 每步結果          │                   │
│  Event Bus ──► WebSocket                       │                   │
│  Store: SQLite → Postgres/TimescaleDB          │                   │
└────────────────────────────────────────────────┼───────────────────┘
                                                 ▼
                              獨立容器：台灣史專家 (RAG / GraphRAG …)
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
| `run.finished` | 聊天框出現最終答案 |

```json
{"type": "agent.started", "run_id": "r_123", "step_id": "s1", "agent": "poet", "ts": "..."}
```

**串流**：`agent.delta` 帶的是「目前為止的完整文字」而不是增量，前端直接取代顯示，所以節流掉事件（每 80ms 最多一次）、
換備援模型或重做時從頭來過都不會錯亂。半成品已先清掉 `<think>`、遮蔽髒話；正式結果仍以通過 harness 檢查的
`agent.finished` / `run.finished` 為準。村長規劃（JSON）不串流。

### 3.7 LLM 註冊與 API Key 加密

**不使用設定檔管理模型**，一律在前端「LLM 設定」頁註冊，資料存在 DB。

#### 註冊流程（UI）

1. 新增 Provider：選類型（`openai_compat` / `anthropic` / `ollama`）、填 `base_url`、填 API key（本地 Ollama 可留空）
2. 按「測試連線」：後端用該設定呼叫 `/v1/models`（Ollama 用 `/api/tags`）列出可用模型
3. 勾選要註冊的模型，可設顯示名稱、是否支援 JSON schema 輸出、context 長度
4. 在「模型分級」把模型拖進 `large` / `small`，排出備援順序
5. 個別 agent 可以在 `agent.yaml` 寫 `model.tier`，也可以在 UI 指定某個模型覆寫

#### 資料表

| 表 | 主要欄位 |
|---|---|
| `llm_providers` | id, name, kind, base_url, `api_key_ct`, `api_key_nonce`, `key_version`, `api_key_hint`(如 `sk-…a1b2`), enabled, created_at, updated_at |
| `llm_models` | id, provider_id, model, display_name, supports_json_schema, context_window, enabled |
| `model_tiers` | tier(`large`/`small`), position, model_id |

#### 加密設計（信封加密的簡化版）

```
主金鑰 (KEK, 32 bytes)  ──不進 DB、不進 git──
   │  AES-256-GCM(nonce=隨機 12 bytes, AAD="llm_provider:{id}:api_key")
   ▼
api_key_ct + api_key_nonce  ──存 SQLite/Postgres──
```

- **演算法**：AES-256-GCM（`cryptography` 套件），每次加密用新的隨機 nonce；帶 AAD 把密文綁定到該筆資料，複製到別的列會解不開
- **主金鑰來源**（依優先順序）：
  1. 環境變數 `VILLAGE_MASTER_KEY`（base64）——上線時由 Docker secret / 雲端 Secret Manager 注入
  2. 金鑰檔 `~/.config/multi-agent-village/master.key`（權限 600，**放在專案資料夾外**）；開發環境第一次啟動自動產生
- **只寫不讀**：API 永遠不回傳明文 key，只回 `api_key_hint`；UI 上是「已設定 / 重新輸入」。更新時沒填 key 就保留原值
- **用時才解密**：只在發出 LLM 請求的當下解密，存在記憶體；log、trace、錯誤訊息都經過遮罩（redaction），不會印出 key
- **輪替**：`uv run village secrets rotate`，用新主金鑰重新加密所有列，`key_version` +1
- **存取控制**：帳號系統（Phase 5）完成前，設定頁 API 預設不驗證；設定 `VILLAGE_ADMIN_TOKEN` 後才要求 Bearer token。即使不驗證，API 也拿不到明文 key（只寫不讀）
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

**資料庫遷移（已完成，`store/migrate.py`、`store/migrations/`）**：Alembic，由程式呼叫（不需 alembic.ini），啟動時 upgrade head。

**記憶與對話（規劃中）**：

- `users`、`conversations`、`messages`：對話紀錄（短期記憶）
- `memories`：長期記憶（使用者偏好、事實），由一個 `memory` 背景 agent 從對話中萃取，問答前檢索相關記憶塞進 context
- `runs`、`steps`、`events`：執行軌跡（日後適合放 TimescaleDB 的 hypertable）
- 一開始 SQLite；用 SQLAlchemy + Alembic，之後切 Postgres 只改 `DATABASE_URL`；向量檢索改用 pgvector

## 4. 目錄結構（規劃）

```
multi-agent-village/
├── backend/
│   ├── pyproject.toml
│   └── village/
│       ├── main.py              # FastAPI 入口、/chat、/ws
│       ├── config.py            # 環境變數（DATABASE_URL、主金鑰位置、bind host）
│       ├── llm/                 # LLMProvider：openai_compat.py, anthropic.py；registry.py（從 DB 載入）
│       ├── security/            # crypto.py（AES-256-GCM 加解密、主金鑰載入、輪替）
│       ├── core/
│       │   ├── card.py          # AgentCard pydantic 模型
│       │   ├── base.py          # BaseAgent / Task / RunContext / AgentResult
│       │   ├── registry.py      # 記憶體中的村民名冊（資料來自 DB）
│       │   ├── prompt_agent.py  # kind=prompt 的通用實作
│       │   ├── remote_agent.py  # kind=remote（A2A client）
│       │   ├── orchestrator.py  # 村長：規劃
│       │   ├── workshop.py      # 工匠：對話打造村民草稿
│       │   ├── reviewer.py      # 審核員：最終答案檢查
│       │   └── executor.py      # DAG 執行 + retry
│       ├── harness/             # input_guard, validators, content_guard
│       ├── events.py            # Event bus
│       └── store/               # SQLAlchemy models
├── agents/                      # 內建範例村民（第一次啟動匯入 DB）
│   ├── poet/agent.yaml
│   ├── jester/agent.yaml
│   ├── reviewer/agent.yaml
├── services/
│   └── taiwan-history/          # Phase 6：獨立容器的 remote agent
├── frontend/
│   ├── public/assets/sprites/   # ★ 角色四向圖、UI 泡泡、manifest.json
│   ├── public/assets/maps/      # Tiled 地圖
│   └── src/ (village/, chat/)
├── scripts/gen_placeholder_sprites.py
├── .env.example                 # 只放非機密設定的範例；真正的 .env 不進 git
├── docker-compose.yml
└── docs/
```
