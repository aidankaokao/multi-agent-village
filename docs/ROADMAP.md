# Roadmap — 由簡到繁

每個階段都是「能跑、能看、能展示」的完整切片。架構細節見 [ARCHITECTURE.md](ARCHITECTURE.md)。

---

## Phase 0 — 地基（約 1~2 天）

- [ ] `backend/`：uv 專案、FastAPI、`/health`
- [ ] SQLite + SQLAlchemy；`.gitignore` 排除 DB、`.env`、金鑰檔
- [ ] `security/crypto.py`：主金鑰載入（環境變數 / 專案外金鑰檔）、AES-256-GCM 加解密，附單元測試
- [ ] LLM 註冊 API：provider 增刪改、測試連線、列出 / 註冊模型、模型分級；API key 只寫不讀
- [ ] `LLMProvider`：OpenAI 相容 client，`chat()` 與 `chat_json(schema)`，從 DB 載入設定
- [ ] 前端「LLM 設定」頁：註冊本地 Ollama 與一個雲端 provider，並指派 `large` / `small`
- [ ] `frontend/`：Vite + React + Phaser，畫出一塊草地，5 個角色用 `assets/sprites` 隨機閒晃（四向走路動畫）
- [ ] WebSocket 連通，後端送假事件 → 指定角色頭頂出現 working 泡泡

**驗收**：打開網頁看到村民在走；按一個 debug 按鈕，詩人頭上冒泡泡；在設定頁註冊 Ollama 和雲端模型後都能測試成功；直接打開 SQLite 只看得到密文。

---

## Phase 1 — MVP：詩人 × 笑話師合作

目標題目：**「寫一首關於貓的詩，然後把它改成笑話」**

- [ ] `AgentCard` / `BaseAgent` / `PromptAgent` / `Registry`
- [ ] `agents/poet/agent.yaml`、`agents/jester/agent.yaml`
- [ ] 村長：產生兩步 Plan（JSON schema 約束 + 修復重試 + `@agent` 指名退路）
- [ ] Executor：依序執行、`{{s1.poem}}` 代入、發事件
- [ ] 聊天面板：送出問題、顯示最終答案、可展開看每一步（誰做了什麼）
- [ ] 村落動畫：村長 → 詩人工作 → 詩人走向笑話師交接 → 笑話師工作 → 完成
- [ ] Harness 最小版：輸出非空 / 長度檢查 + 中英髒話詞表

**驗收**：
1. 單一任務「講個笑話」→ 只有笑話師動
2. 組合任務「寫詩再變笑話」→ 兩位依序動，結果正確串接
3. 把模型換成本地小模型，同樣兩題至少能靠退路完成

**建議測試題**：
- 寫一首關於颱風天的詩，再把它改編成冷笑話
- 講一個工程師的笑話
- @poet 寫一首五言絕句 → @jester

---

## Phase 2 — 教學：自己建立 prompt agent（英文翻譯）

這一階段的重點是「你自己動手」，我負責帶。

1. 複製 `agents/poet/` 為 `agents/translator/`
2. 改 card：`description`、`skills`（`translate`: `{text, target_lang: zh|en}` → `{text}`）、**`examples`**
3. 寫 system prompt：自動偵測語言、只輸出譯文、保留格式
4. 寫 `agents/translator/tests.yaml`：5~10 組輸入 / 期望特徵（例如「輸出不含中文」）
5. `uv run village eval translator` 跑測試，比較雲端 vs 本地模型的表現
6. 存檔 → hot reload → 在村落看到翻譯官出現
7. 試組合題：「寫一首關於海的詩，翻成英文，再改成笑話」→ 三人接力

**附加**：前端「建立村民」表單（填 card + prompt → 產生 yaml），之後新增 prompt agent 不用碰程式碼。

---

## Phase 3 — Harness 強化與審核員

- [ ] `reviewer` agent：用 LLM 判斷最終答案是否合規、是否回答到問題；不合格附理由退回重做（最多 1 次）
- [ ] 失敗回饋迴圈：validator 錯誤訊息帶回 agent 重試
- [ ] 每步 timeout、使用者取消
- [ ] Trace 存 DB，前端「執行紀錄」頁可看每一步輸入輸出、耗時、token
- [ ] 平行步驟（例如同時翻成英文與日文）

---

## Phase 4 — Tool agent（需要寫程式的村民）

- [ ] `calculator`：LLM 只負責把自然語言轉成運算式，計算用安全求值器（不用 `eval`）。示範「弱模型也能精準」的設計
- [ ] `chart`：輸入表格資料（CSV/JSON）+ 需求 → 產生圖表（Vega-Lite spec 給前端渲染，或 matplotlib PNG）
- [ ] 支援 agent 輸出「附件」（圖片、檔案）並在聊天框顯示

---

## Phase 5 — 帳號與長期記憶

- [ ] 簡單登入（先用使用者名稱 + token）
- [ ] 對話紀錄、多輪上下文
- [ ] `memory` agent：萃取偏好/事實存 `memories`，提問前檢索注入
- [ ] Alembic migration；準備好切 Postgres

---

## Phase 6 — Remote agent：台灣史專家（獨立容器）

- [ ] `services/taiwan-history/`：獨立 FastAPI 服務，實作 A2A（`/.well-known/agent-card.json` + `message/send`）
- [ ] 先做基本 RAG（資料切塊 + 向量檢索 + 引用來源），再進化到 GraphRAG / agentic RAG
- [ ] 主系統 `agents/taiwan-history/agent.yaml` 只填 `kind: remote` + `endpoint`
- [ ] `docker-compose.yml`：backend、frontend、taiwan-history、(postgres)
- [ ] 健康檢查：remote agent 掛掉時，村民在村落裡顯示「外出中」並從可用名單移除

---

## Phase 7+ — 之後的想像

- Postgres + TimescaleDB（事件與指標）、pgvector（記憶與 card 檢索）
- Agent 之間直接對話（不經村長），例如詩人主動請翻譯官幫忙
- 市集頁面：瀏覽 / 啟用 / 停用 / 匯入外部 A2A agent
- 村落養成：常被叫用的村民升級、房子變大；任務看板
- 多使用者同時在村裡看得到彼此的任務
