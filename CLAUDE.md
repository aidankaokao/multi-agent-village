# Multi-Agent Village

像素風虛擬村落：每個 agent 是一位村民，村長（orchestrator）依 Agent Card 規劃並指派村民合作完成使用者的問題。

- 架構：`docs/ARCHITECTURE.md`（全景、事件表、加密、帳號 / 對話 / 記憶、目錄結構）
- 階段規劃：`docs/ROADMAP.md`
- 素材規格：`docs/SPRITES.md`

## 目前進度

Phase 0～5 完成：村民管理與工坊（工匠）、審核員 / 執行紀錄 / 村規 / 平行 / 停止、工具型村民（計算機、圖表 SVG、日期）、
帳號（登入頁示範村落）、每個帳號自己的 LLM、對話與多輪上下文、書記與長期記憶。DB 遷移到 0006。

**進行中：Phase 5V 視覺模型**（`docs/ROADMAP.md`）。A 完成（訊息帶圖、`vision` 分級、看圖測驗）；
接著 B（💬 附圖上傳與存放）、C（系統角色「鑑定師」看圖，追問時重看原圖，圖片跟著對話保存）。
生圖模型等視覺做完再討論。Phase 6（台灣史 remote agent，獨立容器 RAG）暫緩。

## 使用者偏好（重要）

- 一律用繁體中文溝通與撰寫文件。
- 使用者是「介面使用者」（雖然有開發者權限）：所有功能都要能在前端操作，設定存 DB；不要求使用者改 yaml 或後端程式。
  新功能先設計介面上的管理方式。檔案（例如 `agents/*/agent.yaml`）只是第一次啟動匯入的預設值。
- 教學要給具體、可一步一步照做的步驟。
- 部署：`./build.sh` 只 build image；`docker-compose up -d` 由使用者自己執行（本機只有 docker-compose，無 compose plugin）。
  每次改完要 build，並提醒使用者部署與 `Cmd + Shift + R`。
- 大的設計決定先提方案再做；使用者會先部署確認再說「下一步」。
- 使用者已授權 commit 與 push（remote `git@github.com:aidankaokao/multi-agent-village.git`，分支 `main`）。

## 原則

- LLM 可能是雲端，也可能是本地弱模型（Ollama）。任何依賴 LLM 的流程都要有結構化輸出約束、失敗重試與非 LLM 的退路。
  OpenAI 相容 client 會自動適應各家參數差異（max_completion_tokens、不收 temperature、reasoning_effort 的 none/minimal/low），
  JSON 輸出被截斷時換下一個模型；模型取消勾選「支援 JSON schema」= 不送約束照樣使用。
- 規劃（LLM）與執行（程式碼）分開：村長只產生 Plan JSON（`steps`、`use`，`"prev"` = 上一輪的回答），Executor 負責執行。
- 系統角色（保留名稱，不會被派去做問答）：`mayor` 村長、`craftsman` 工匠（`core/workshop.py`，對話打造村民）、
  `reviewer` 審核員（`core/reviewer.py`）、`scribe` 書記（`core/scribe.py`，長期記憶）；規劃中：鑑定師（看圖）。
- 視覺模型自成 `vision` 分級（`tier:vision`），不和 large / small 互相備援；只有通過看圖測驗（`llm/vision_test.py`，
  `llm_models.vision_verified_at`）的模型會被用到。一般 LLM 流程不可以用到視覺模型。
- 村民類型：對話型（prompt）與工具型（tool，工具在 `backend/village/tools/`；LLM 只整理參數，計算交給程式，並有退路）。
  年份 / 今天日期這類 LLM 不知道的事一律交給程式決定。
- 驗證 / 審核 / 重試放在 harness 層，不寫進個別 agent。全域行為是「村規」（`settings` 表，「村規」分頁）。
- 後端每個動作都要發事件，前端村落動畫只靠事件驅動；別人的問答事件只送動畫欄位（`events.for_viewer`）。
- 帳號：第一位註冊者是管理員；成員可以問答、看自己的紀錄 / 記憶、管理自己的 LLM；管理員另外管理村民、村規、帳號。
  權限用 `require_user` / `require_admin`（`security/auth.py`）。
- LLM 設定屬於各帳號（`llm_providers.user_id`、`model_tiers.user_id`）：用 context variable `llm_user` 決定是誰的模型
  （問答由 Runner 設定；試打 / 工匠 / LLM 試打由 API 端點設定）。沒設定時問答回 409 提醒。
  API key 以 AES-256-GCM 加密存 DB（AAD 綁定擁有者與 provider），API 不回傳明文，log 要遮罩。
- Runner 是全村共用的：每次問答各自的狀態（上一輪的回答、記憶）放 context variable，不能放在 Runner 物件上。
- 不可 commit：`*.db`、`data/`、`.env`、`*.key` 或任何 key。主金鑰放專案外（`~/.config/multi-agent-village/master.key`）或 `VILLAGE_MASTER_KEY`。

## 介面配置

- 登入頁：示範村落（`frontend/src/auth/demo.ts` 劇本驅動假事件）＋ 登入 / 註冊。
- 登入後：像素地圖滿版；左上帳號選單（LLM 設定、改密碼、登出、刪除帳號）；右上 ⚙️（滑鼠移入展開）→ 右側抽屜
  （村民、紀錄、記憶、村規、帳號、LLM 設定，依角色顯示）；右下 💬 問答視窗（對話列表、執行過程、停止、圖片放大）。
- 彈窗用 `ui/Modal.tsx`（portal 到 body，打開有動畫）；表單裡的非送出按鈕要寫 `type="button"`。

## 指令

- 後端：`cd backend && uv run village serve`（127.0.0.1:8000）；測試 `uv run pytest`
- 前端：`cd frontend && npm run dev`（5173，/api 與 /ws 代理到後端）；型別檢查 `npx tsc -b`
- 忘記密碼：`village reset-password <帳號>`；直接建管理員：`village create-admin <帳號>`（容器內加 `docker-compose exec backend`）
- 主金鑰輪替：`village rotate-key`
- 重新產生 sprites（角色、裝飾、manifest）：`uv run --with pillow python scripts/gen_placeholder_sprites.py`
- 容器部署：`cp .env.example .env` 填 `FRONTEND_PORT` → `./build.sh` → 使用者自己 `docker-compose up -d`；前端綁 0.0.0.0
- DB 結構用 Alembic（`backend/village/store/migrations/`），啟動時自動 upgrade head；create_all 時代的舊 DB 先標記 0001 再升級。
  改 models 後：`cd backend && uv run python -m village.store.migrate revision -m "說明"`（外鍵要命名；SQLite 用 batch 模式）。
  已加 asyncpg，切 Postgres 只改 `DATABASE_URL=postgresql+asyncpg://…`

## 踩過的坑

- `cryptography` 釘在 `<47`：47+ 的 aarch64 wheel 在本機 Docker VM 會 SIGILL（exit 132）。
- 不要用 `npx prettier`：專案沒裝，npx 會停下來等安裝確認而卡住；也會把檔案改成 80 字寬，跟專案風格不同。
- 背景問答在關閉時要先等收尾再 dispose DB（`main.py` lifespan），否則被取消的問答寫 DB 會卡住整個程序（測試偶發卡住也是這個）。
- e2e 截圖：scratchpad 有 CDP 腳本（shot_*.py）；用 `uv run --with websockets python …`；填問答框要指定 `.workshop` / `.chat-input` 等範圍，
  避免填到隱藏的元素；CDP 取陣列要 `JSON.stringify`。
- 本機 Ollama 有 qwen3.5:9b（e2e 用，思考強度設 none）；使用者的容器用 gpt-5、gpt-4o-mini、DeepSeek（OpenRouter）。
  OpenRouter 的 GLM 5.3 線路不穩（JSON 約束下吐空白、無約束時偶爾亂碼），建議不要放 large 第一位。
