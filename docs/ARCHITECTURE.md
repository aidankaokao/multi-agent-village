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
| `prompt` | 只寫 `agent.yaml` | 詩人、笑話師、英文翻譯 | 後端同程序 |
| `tool` | `agent.yaml` + `agent.py`（繼承 `BaseAgent`） | 計算機（不靠 LLM，用安全的運算式求值）、畫圖表（matplotlib / Vega-Lite） | 後端同程序 |
| `remote` | `agent.yaml` 只填 `endpoint`，實際程式在另一個容器 | 台灣史專家（RAG / GraphRAG / agentic RAG） | 獨立容器，走 A2A |

所有類型都實作同一個介面：

```python
class BaseAgent(Protocol):
    card: AgentCard
    async def run(self, task: Task, ctx: RunContext) -> AgentResult: ...
# ctx 提供：llm、emit(event)、memory、user、取消訊號
```

Registry 啟動時掃描 `agents/*/agent.yaml`，開發模式下檔案變動自動重新載入（hot reload）。

### 3.3 Orchestrator（村長）

村長本身也是一個 agent，但特殊：它的輸入是使用者訊息，輸出是 **Plan**。

```json
{
  "steps": [
    {"id": "s1", "agent": "poet",   "skill": "write_poem", "input": {"topic": "貓"}},
    {"id": "s2", "agent": "jester", "skill": "make_joke",  "input": {"text": "{{s1.poem}}"}, "after": ["s1"]}
  ],
  "final": "s2"
}
```

執行器（Executor）照 DAG 跑：沒有依賴的步驟平行跑，`{{s1.poem}}` 由前一步的輸出代入。
這樣 **規劃（LLM 做）** 和 **執行（程式碼做）** 分開——弱模型只需要做好「挑人+填參數」這一件事。

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

**審核員**本身也是一個 agent（`reviewer`），在村落裡也看得到它出來工作。

### 3.6 事件流（驅動村落動畫）

後端每個動作都發一個事件，前端據此動畫：

| 事件 | 前端效果 |
|---|---|
| `run.started` | 村長頭頂出現 💬 |
| `plan.created` | 村長走向第一位被指派者 |
| `agent.started` | 該角色停止閒晃、頭頂 working 泡泡動畫 |
| `agent.handoff` (from→to) | 角色走向下一位（帶著卷軸） |
| `agent.finished` / `agent.error` | 泡泡變 ✓ / ❗，幾秒後恢復閒晃 |
| `review.started/finished` | 審核員出動 |
| `run.finished` | 聊天框出現最終答案 |

```json
{"type": "agent.started", "run_id": "r_123", "step_id": "s1", "agent": "poet", "ts": "..."}
```

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
   │  AES-256-GCM(nonce=隨機 12 bytes, AAD="llm_provider:{id}:v{key_version}")
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
- **存取控制**：設定頁 API 需要 admin 身分。帳號系統（Phase 5）完成前，後端預設只綁 `127.0.0.1`，並要求一個啟動時產生的 admin token
- **git 防線**：`.gitignore` 排除 `*.db`、`data/`、`.env`、`*.key`；另可加 pre-commit 的 secret 掃描（gitleaks）

#### 這個設計擋得住什麼

| 情境 | 結果 |
|---|---|
| 專案推到 GitHub | 沒有任何 key 或 DB 進 git |
| DB 檔或備份外流 | 只有密文，沒有主金鑰解不開 |
| 有人連到 API | 拿不到明文，只看得到 hint |
| 攻擊者同時拿到 DB **和** 主金鑰（整台主機被控制） | 擋不住——這是所有應用層加密的極限，要靠主機安全與把主金鑰交給 Secret Manager / KMS 管理來降低風險 |

### 3.8 記憶與帳號（後期）

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
│       │   ├── registry.py      # 掃描 agents/、hot reload
│       │   ├── prompt_agent.py  # kind=prompt 的通用實作
│       │   ├── remote_agent.py  # kind=remote（A2A client）
│       │   ├── orchestrator.py  # 村長：規劃
│       │   └── executor.py      # DAG 執行 + retry
│       ├── harness/             # input_guard, validators, content_guard
│       ├── events.py            # Event bus
│       └── store/               # SQLAlchemy models
├── agents/                      # ★ 市集：每個資料夾一位村民
│   ├── poet/agent.yaml
│   ├── jester/agent.yaml
│   ├── reviewer/agent.yaml
│   └── translator/agent.yaml    # Phase 2 教學
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
