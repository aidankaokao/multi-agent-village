# Multi-Agent Village

像素風虛擬村落：每個 agent 是一位村民，村長（orchestrator）依 Agent Card 規劃並指派村民合作完成使用者的問題。

- 架構：`docs/ARCHITECTURE.md`（全景、事件表、加密、帳號 / 對話 / 記憶、目錄結構）
- 外接 agent：`extra-agents/`（每個子資料夾是獨立服務，各有 PLAN.md、README、docker-compose、build.sh）
- 階段規劃：`docs/ROADMAP.md`
- 素材規格：`docs/SPRITES.md`

## 目前進度

全部經使用者部署實測。DB 遷移到 0009。

- **Phase 0～5**：村民管理與工坊（工匠）、審核員 / 執行紀錄 / 村規 / 平行 / 停止、工具型村民（計算機、圖表 SVG、日期）、
  帳號（登入頁示範村落）、每個帳號自己的 LLM、對話與多輪上下文、書記與長期記憶。
- **Phase 5V 視覺**：`vision` 分級與看圖測驗、💬 附圖（前端縮圖去 EXIF、小圖放大、`chat_images`、`/api/images/{id}` 只有本人看得到）、
  系統角色「鑑定師」看圖（清單類一律完整抄寫、追問時重看原圖）。
- **Phase 6G 生圖**：只接 OpenRouter（`POST /images`）、`image` 分級、生圖測驗、畫家村民（`tools/painter.py` + `core/painter.py`）、
  村規張數上限、畫作 14 天過期、修圖（附圖或上一張畫作當參考圖，`input_references`）。
- **Phase 6 台灣史專家（外接 agent）**：`extra-agents/taiwan-history/`（獨立專案，規劃與進度在它的 `PLAN.md`）。
  中文維基 175 篇 → bge-m3 + SQLite 混合檢索 → 呼叫端自帶 LLM（header）→ 附引用的回答；資料沒有的可用模型知識補充並標示
  「以下非資料內容：」；A2A（`message/send`、`message/stream`）、存取 token、簡易測試網頁、獨立 docker-compose（127.0.0.1:8100）。
  村子這邊叫「**駐村專家**」（村民 `kind: remote`）：邀請 / 管理介面、token 加密、健康檢查與外出中、村長派工、共享提問者 LLM 的開關、
  串流顯示。待改進（以後再說）：「起因 / 近因」檢索、測試題、回答風格。
- **地圖互動**：點村民開始問答（自動 @）、村民冒泡自我介紹、村民之間聊天（頻率使用者覺得剛好）。

**下一步**：使用者 compact 後要討論下一步做什麼（還沒決定）。本機生圖（Ollama 目前不支援，見「踩過的坑」）暫不做。

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
  `reviewer` 審核員（`core/reviewer.py`）、`scribe` 書記（`core/scribe.py`，長期記憶）、
  `appraiser` 鑑定師（`core/appraiser.py`，有附圖時先看圖，結果放 context variable `_APPRAISAL` 交給村長 / 村民 / 審核員）。
- 駐村專家（外接的獨立 agent）：村民 `kind: remote`（`core/remote.py`、`api/residents.py`），
  存取 token 加密存 `remote_agents`；呼叫網址用「管理員填的網址 ＋ card 的路徑」；村子容器連主機服務用 `host.docker.internal`。
  「允許把提問者的 LLM 設定交給它」（`share_llm`）預設關閉；打開時送提問者 large 分級第一個 OpenAI 相容模型
  （`LLMService.shareable`）。村長只派工給「在村」的駐村專家（`RemoteDirectory.online_ids`）；呼叫狀態放 context variable
  `REMOTE_ENV`（問答與村民試打各自設定）。
- 視覺模型自成 `vision` 分級（`tier:vision`），不和 large / small 互相備援；只有通過看圖測驗（`llm/vision_test.py`，
  `llm_models.vision_verified_at`）的模型會被用到。一般 LLM 流程不可以用到視覺模型。
- 生圖模型（`llm_models.output="image"`）只能放 `image` 分級、只能透過 `LLMService.generate_image` 使用（OpenRouter `POST /images`），
  不能拿來對話；只有通過生圖測驗（`image_verified_at`）的會被用到。只收點陣圖（看檔頭），SVG 不收。
  畫作存 `chat_images`（kind=generated），14 天後過期（`GENERATED_DAYS`，main.py 定期清理）。
- 村民類型：對話型（prompt）與工具型（tool，工具在 `backend/village/tools/`；LLM 只整理參數，計算交給程式，並有退路）。
  需要外部服務的工具覆寫 `arun`（例如畫家）；每次執行都花錢的工具設 `costly = True`（審核員退件不重做）。
  畫家的作畫環境（誰、哪段對話、張數上限）放 context variable `PAINT_ENV`（問答與村民試打各自設定）。
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
- 地圖互動（`village/VillageScene.ts`）：閒著的村民每 8～15 秒有人冒一句（台詞由 `App.tsx` 的 `idleLines` 從名片產生，
  系統角色在 `MAYOR` 等常數裡）；每 30～60 秒兩位附近的村民湊在一起聊幾句；派工時立刻中斷。
  點村民：開新對話並在輸入框填「@id 」（村長不加 @；工匠、審核員、書記、鑑定師只自我介紹；外出中的駐村專家說外出中）。
  開發模式有 `window.__village`（e2e 測試用）。

## 指令

- 後端：`cd backend && uv run village serve`（127.0.0.1:8000）；測試 `uv run pytest`
- 前端：`cd frontend && npm run dev`（5173，/api 與 /ws 代理到後端）；型別檢查 `npx tsc -b`
- 忘記密碼：`village reset-password <帳號>`；直接建管理員：`village create-admin <帳號>`（容器內加 `docker-compose exec backend`）
- 主金鑰輪替：`village rotate-key`（API key 與駐村專家的存取 token 一起重新加密）
- 台灣史專家（`extra-agents/taiwan-history/`）：`uv run th index | search | ask | serve | gen-token`、`uv run pytest`；
  部署 `./build.sh` → 使用者自己 `docker-compose up -d`（在那個資料夾）。兩邊都要更新時：**先部署專家、再部署村子**
- 重新產生 sprites（角色、裝飾、manifest）：`uv run --with pillow python scripts/gen_placeholder_sprites.py`；頁籤 icon：`scripts/gen_favicon.py`（同樣用法）
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
- 使用者的 OpenRouter key 有總額度上限（已從 5 美元調到 10 美元）。出現 403「Key limit exceeded」是額度用完，不是程式問題；
  要排查時進容器看執行紀錄（`docker-compose exec -T backend python`，DB 在 `/data/village.db`，`runs` 表的 plan / steps）。
- gpt-4o-mini 讀中文小字（菜單）會看錯字，換一次結果就不同；需要準確時把 gpt-5 或 Qwen VL 排在 vision 分級前面。
- Ollama 生圖：2026/1 曾在 macOS 推出實驗性生圖（x/flux2-klein、x/z-image-turbo，只有 CLI），但 2026/10 的原始碼
  （`server/routes.go`、`cmd/cmd.go`）會拒絕生圖模型（「image generation models are not currently supported」），
  本機 0.40.5 也沒有 `/v1/images/generations`。使用者決定先只用 OpenRouter；Ollama 恢復支援再寫轉接（`LLMClient.generate_image`）。
- pytest 偶爾卡住（某個測試失敗時沒正常收尾）：用 `perl -e 'alarm 90; exec @ARGV' uv run pytest -q` 跑，逐檔找出卡住的；
  卡住後記得 `pkill -f pytest`。
- 村子在容器裡：連主機上的服務（Ollama、駐村專家）要用 `host.docker.internal`，不能用 localhost；主機本身不認得這個名稱
  （本機開發模式的 e2e 測試要用 localhost）。綁 127.0.0.1 的服務從容器用 host.docker.internal 連得到（已實測）。
- 駐村專家不支援串流時村子會記住（`remote._NO_STREAM`，記憶體），重啟村子才會再試；所以要先部署新版專家再部署村子。
- 村長規劃的「移除沒要求的步驟」（`prune_unrequested`）：除了關鍵字，也比對指示是不是請求的原話（字的兩兩組合六成以上）；
  會花錢的村民（畫家）重複出現且不是原話的步驟會被移除。
