# Multi-Agent Village

像素風虛擬村落：每個 agent 是一位村民，村長（orchestrator）依 Agent Card 規劃並指派村民合作完成使用者在問答框的任務。

- 架構：`docs/ARCHITECTURE.md`
- 階段規劃：`docs/ROADMAP.md`（目前：Phase 0 完成，下一步 Phase 1 MVP）
- 素材規格：`docs/SPRITES.md`

## 原則

- 一律用繁體中文溝通與撰寫文件。
- LLM 可能是雲端，也可能是本地弱模型（Ollama）。任何依賴 LLM 的流程都要有結構化輸出約束、失敗重試與非 LLM 的退路。
- 規劃（LLM）與執行（程式碼）分開：村長只產生 Plan JSON，Executor 負責執行。
- 新增 prompt 型 agent 只能動 `agents/<id>/agent.yaml`，不應需要改後端程式。
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
