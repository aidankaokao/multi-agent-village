# Multi-Agent Village

像素風虛擬村落：每個 agent 是一位村民，村長（orchestrator）依 Agent Card 規劃並指派村民合作完成使用者在問答框的任務。

- 架構：`docs/ARCHITECTURE.md`
- 階段規劃：`docs/ROADMAP.md`（目前：Phase 4 工具型村民完成，下一步 Phase 5 帳號與長期記憶）
- 素材規格：`docs/SPRITES.md`

## 原則

- 一律用繁體中文溝通與撰寫文件。
- LLM 可能是雲端，也可能是本地弱模型（Ollama）。任何依賴 LLM 的流程都要有結構化輸出約束、失敗重試與非 LLM 的退路。
- 規劃（LLM）與執行（程式碼）分開：村長只產生 Plan JSON，Executor 負責執行。
- 使用者是「介面使用者」：新增 / 編輯 / 測試村民一律在前端「村民」分頁完成，存 DB（`agents` 表），不要求使用者改檔案或後端程式。`agents/<id>/agent.yaml` 只是第一次啟動時匯入的內建範例（DB 沒有該 id 才匯入；刪除的內建範例不會自動回來）。`mayor`、`craftsman`（工匠，`core/workshop.py`：對話打造村民草稿）、`reviewer`（審核員，`core/reviewer.py`）是保留名稱。
- 核心流程在 `backend/village/core/`：`orchestrator.py`（村長規劃與退路）、`executor.py`（執行、平行、逾時、取消、事件、寫紀錄）、`harness.py`（輸出檢查）、`reviewer.py`（審核員）。
- 工具型村民：工具（可靠的程式碼）在 `backend/village/tools/`，使用者在介面挑工具；LLM 只整理參數，計算交給程式，並有不靠 LLM 的退路。
- 全域行為（審核員、逾時、平行、紀錄保留）是「村規」，存 `settings` 表、在前端「村規」分頁調整，不寫進設定檔。
- 驗證 / 審核 / 重試放在 harness 層，不寫進個別 agent。
- 後端每個 agent 動作都要發事件，前端村落動畫只靠事件驅動。
- LLM provider / 模型只在 UI 註冊並存 DB，不寫進設定檔。API key 一律以 AES-256-GCM 加密後存 DB，API 不回傳明文，log 要遮罩。
- 不可 commit：`*.db`、`data/`、`.env`、`*.key` 或任何 key。主金鑰放專案外（`~/.config/multi-agent-village/master.key`）或環境變數 `VILLAGE_MASTER_KEY`。

## Git

- remote：`git@github.com:aidankaokao/multi-agent-village.git`，分支 `main`；使用者已授權 commit 與 push。

## 指令

- 後端：`cd backend && uv run village serve`（127.0.0.1:8000）；測試 `uv run pytest`
- 前端：`cd frontend && npm run dev`（5173，/api 與 /ws 代理到後端）；型別檢查 `npx tsc -b`
- admin token 為選用：只有設定 `VILLAGE_ADMIN_TOKEN` 時設定 API 才驗證（使用者覺得麻煩，預設關閉）
- 主金鑰輪替：`cd backend && uv run village rotate-key`
- 重新產生佔位 sprites：`python3 scripts/gen_placeholder_sprites.py`
- 容器部署：`cp .env.example .env` 填 `FRONTEND_PORT` → `./build.sh`（只 build image）→ 使用者自己執行 `docker-compose up -d`；前端綁 0.0.0.0。本機只有 docker-compose（無 docker compose plugin）
- `cryptography` 釘在 `<47`：47+ 的 aarch64 wheel 在本機 Docker VM 會 SIGILL（exit 132）
- DB schema 目前用 `create_all`，改欄位時要刪掉 `data/village.db` 重建（Phase 5 前導入 Alembic）
