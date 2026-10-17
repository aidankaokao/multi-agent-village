# 台灣史專家：規劃與進度

> 最後更新：2026-10-17（T4 完成，等使用者部署實測）。每完成一步就更新這份文件（勾選、補上實際做法與決定）。

## 已確定的決定

1. **獨立開發、獨立部署**：放在 `extra-agents/taiwan-history/`，有自己的程式、`docker-compose.yaml`、`.env`；
   不 import 村子（`backend/`）的任何程式碼，也不和村子共用資料庫。
2. **開發完成後再接回村子**：村子用 HTTP 呼叫它（外接 agent）。村子那邊要做的是「外接村民」功能（見 T4），
   之後其他外接 agent 也走同一套。
3. **資料來源要先蒐集**：使用者手上沒有台灣史資料，由 Claude 從網路公開資料（先以中文維基百科為主）抓取、
   整理成文件，當作 RAG 的資料來源。
4. **資料裡沒有的內容**：可以用模型自己的知識補充，但要放在回答最後一段、開頭寫「以下非資料內容：」、不標引用編號
   （程式也會移除那段的編號；引用清單只算前面有出處的部分；測試網頁把那段用虛線框起來）。
   回答的其他範圍、風格（非台灣史問題、長度、爭議議題）與資料清單的增減，之後再說。
5. **自己有一個簡易的網頁問答介面**（不用華麗）：在這個獨立專案裡單獨測試台灣史專家的問答。
   這裡測完沒問題（＝這個獨立專案完成）之後，才回主專案做對接（T4）。
6. **回答用的 LLM 由呼叫端每次帶進來**（呼叫端自帶金鑰）：網址、模型、API key 放在 HTTP header
   （`X-LLM-Base-URL`、`X-LLM-Model`、`X-LLM-API-Key`），agent 用完就丟，不存檔、不寫 log、不回傳。
   好處：agent 不用保管 LLM 的 key；接回村子後每個帳號用自己的 LLM 與額度。
   - 呼叫端給的網址必須在允許清單裡（`.env` 的 `TH_ALLOWED_LLM_HOSTS`，預設 OpenRouter、OpenAI、本機），避免被拿去探測內網
   - 跨機器呼叫時要走 HTTPS（key 在網路上傳）
7. **embedding 固定用本地 Ollama 的 bge-m3**：資料與問題必須用同一個模型，所以不給呼叫端選；不需要任何 key。
8. **簡易問答網頁比照「另一個應用程式呼叫 agent API」**：網頁裡設定模型網址、模型、key，每次發問帶上；
   key 預設只存在這個分頁，勾「記住」時在瀏覽器裡加密（WebCrypto AES-GCM）後才保存。
   （2026-10-17 改：原本用使用者自訂密碼加密，每次打開都要輸入密碼，使用者覺得多此一舉；改成瀏覽器自己產生、
   無法匯出的金鑰（存在 IndexedDB），打開自動帶入。舊格式的設定解開一次就自動轉成新格式。文件彙整師的測試網頁同一套。）

## 資料夾結構（預計）

```
extra-agents/taiwan-history/
├── PLAN.md               這份文件
├── pyproject.toml        獨立的 Python 專案（uv 管理）
├── README.md             介紹、怎麼部署、怎麼接進村子
├── .env.example          設定範本（LLM、embedding、port、對接用的 token）
├── docker-compose.yaml   獨立部署（之後建立）
├── corpus/               整理好的資料文件（Markdown，每篇附來源網址與授權），進 git
│   └── sources.yaml      要抓哪些條目（清單可以增減，重新抓取時照這份）
├── scripts/              抓資料、整理文件的工具
├── app/                  服務程式（檢索、回答、A2A 介面、簡易問答網頁）
├── tests/
└── data/                 向量索引等執行資料（從 corpus 重建，不進 git）
```

## 階段與進度

### T0：規劃 ✅
- [x] 建立 `extra-agents/` 與本資料夾、寫下規劃

### T1：資料蒐集 ✅
- [x] 擬定條目清單 `corpus/sources.yaml`（草稿，等使用者確認）：10 類共 175 篇，全部用維基百科 API 確認存在
      （總論 11、史前 11、原住民族 20、荷西 12、明鄭 7、清治 23、割臺 8、日治 31、戰後與戒嚴 33、民主化與當代 19），
      原始資料約 8.5 MB（wikitext）。排除了通用條目（白色恐怖→改臺灣白色恐怖時期、樟腦、保甲制度）與過短的小條目
- [x] 抓取工具 `scripts/fetch_wiki.py`：TextExtracts API（純文字，已去掉表格、資訊框、註腳）→ Markdown
      （`== 標題 ==` 轉 `##`；去掉參考文獻、外部連結、參見等章節與空標題；一篇失敗不影響其他篇；被限速會等待重試）
      用法：`uv run python scripts/fetch_wiki.py`（只抓沒抓過的）、`--refresh`（全部重抓）、`--only 標題…`
- [x] 每篇文件開頭（YAML front matter）記錄：標題、維基條目名稱、網址、版本（revision）、抓取日期、時期、授權
- [x] 統一用臺灣正體（`variant=zh-tw`；簡體標題的條目內容也會轉換）
- [x] 資料量與品質檢查（`scripts/corpus_stats.py`）：175 篇全部抓到、約 131 萬字（不含空白），`corpus/docs/` 約 3.9 MB；
      沒有模板、wiki 標記、HTML 殘留（「臺北帝國大學」的簡體字警告是日文原文，不是問題）。
      最短：營埔文化、卑南文化、郁永河（數百字）；最長：客家裔臺灣人、二二八事件、李登輝（3～5 萬字）
- [x] 使用者決定：清單先全部保留（含爭議議題），增減之後再說

### T2：RAG 服務本體
- [x] 切塊（`app/chunker.py`）：依章節切，約 600 字一塊，過長段落依句號切，太短的章節併到下一塊；
      每塊帶「條目 › 章節」路徑（向量化時也帶上）。175 篇 → 3,389 塊，平均約 390 字
- [x] 向量化（`app/embedder.py`）：本地 Ollama bge-m3（1024 維、正規化）
- [x] 索引（`app/store.py`、`app/indexer.py`）：SQLite 單檔 `data/index.sqlite3`（向量存 BLOB，查詢時載入記憶體用 numpy 算）；
      增量更新（內容指紋沒變的文件不重做、刪掉的文件移除）；換 embedding 模型自動整個重建
- [x] 檢索：向量（語意）＋關鍵字（中文兩字一組、英文單字與年份，idf 加權）→ 名次融合（RRF）
- [x] 回答（`app/answer.py`、`app/llm.py`）：LLM 只根據檢索到的段落回答、標 [編號] 引用，回傳實際引用的來源（條目、章節、網址）；
      資料不足就說「資料中沒有提到」；LLM 不能用時直接交出找到的段落。LLM 由呼叫端帶進來（見決定 6），
      自動適應 gpt-5 的 max_completion_tokens、不收 reasoning_effort 的模型
- [x] HTTP 服務（`app/main.py`）：`GET /health`、`GET /search?q=`（只檢索）、`POST /ask`（LLM 設定放 header）
- [x] 指令（`uv run th …`）：`index`（`--rebuild`）、`search`、`ask`、`serve`
- [x] 單元測試 12 個（不連 Ollama、不連真的 LLM）
- [x] 建好索引：175 篇、3,389 塊，`data/index.sqlite3` 約 19 MB；本機建一次約 7 分鐘
- [x] 實測（本機 qwen3.5:9b）：檢索約 0.3 秒、回答 45～60 秒；三題都答出來並附引用
- [ ] 待改進：問「霧社事件的**起因**」時沒抓到「事件**近因**」章節（字面對不上），回答混進第二次霧社事件的內容
      → 之後試「多問法改寫」（請 LLM 把問題改寫成幾種說法再檢索）或重新排序（rerank）
- [ ] 測試題：一組有標準答案的問題，量檢索與回答的正確率（改進檢索時當基準）

### T3：對外介面與獨立部署 ✅
- [x] 存取 token（`app/auth.py`）：`Authorization: Bearer …`，可設多個（`.env` 的 `TH_ACCESS_TOKENS`）；
      沒設定時一律拒絕（503）；固定時間比較。`./build.sh` 在 .env 沒有 token 時自動產生一個；`th gen-token` 也能產生
- [x] A2A（`app/a2a.py`）：`GET /.well-known/agent-card.json`（公開，含技能、範例、需要的 LLM header）、
      `POST /a2a`（JSON-RPC 2.0 `message/send`；回覆 Message 的 parts = text 回答 ＋ data 引用）；LLM 一樣放 header
- [x] 簡易問答網頁（`app/static/index.html`，`GET /` 公開）：連線設定（token、LLM 網址、模型、key、思考強度、
      OpenRouter / 本機 Ollama 快速填入）；「記住」時用自訂密碼 PBKDF2 + AES-GCM 加密存在 localStorage
      （只能在 localhost 或 https 使用）；回答的 [編號] 可點、跳到原文段落；「只看找到的資料」不呼叫 LLM；顯示服務與建索引進度
- [x] 獨立部署：`Dockerfile`（非 root、corpus 打包進 image、索引在 volume `th-data`）、`docker-compose.yaml`
      （預設只綁 127.0.0.1:8100、連主機 Ollama）、`.env.example`、`./build.sh`（只 build）；啟動時自動補建索引
- [x] 測試 14 個（含 token、A2A）；本機瀏覽器實測：加密保存（localStorage 只有密文）、錯誤密碼、解鎖、問答、引用跳轉
- [x] 使用者部署實測（README 的「部署」與「測試網頁」步驟）

### T4：接回村子（改的是村子這邊）
外接的 agent 在村子裡叫「**駐村專家**」（和村子自己打造的「村民」區分）。

- [x] A：資料與管理（村子 DB 遷移 0009）
  - 村民定義新增 `kind: remote`（`remote: {url, a2a_path, share_llm}`）；`remote_agents` 表存加密的存取 token
    （AES-256-GCM，AAD 綁定這位專家；`village rotate-key` 也會重新加密）、讀到的 agent card、健康狀態
  - 管理 API `/api/residents`（管理員）：讀取名片（preview）、邀請入村、改網址 / token / 共享 LLM 開關、重新讀取名片、立刻檢查；
    改名字、外觀、停用、刪除沿用村民 API
  - 每分鐘健康檢查（`/health`，沒有就讀 agent card；`ok: false` 也算外出中），狀態改變發 `residents.status` 事件
  - 介面：村民頁「🧳 邀請駐村專家」（填網址與 token → 讀取名片 → 確認 id、名字、外觀、共享開關）、
    列表顯示「🧳 駐村專家」與「🟢 在村 / 🔴 外出中」、「管理」視窗
  - 「允許把提問者的 LLM 設定交給它」預設關閉，打開時要確認
  - 網路：村子的容器用 `http://host.docker.internal:8100` 連得到主機上只綁 127.0.0.1 的台灣史專家（已實測）；
    呼叫時用「管理員填的網址 ＋ card 裡的路徑」（card 寫的 localhost 從容器連不到）
- [x] B：派工與呼叫
  - 村長名冊只放「在村」的駐村專家（外出中的不派工）；執行時 A2A `message/send`（指示＋前一步成果），帶 Bearer token
  - 共享開關打開時，把提問者 large 分級第一個 OpenAI 相容模型（網址、模型、key）放進 X-LLM-* header；
    Anthropic 不相容會略過並提醒。token、key 只在 header，不進執行紀錄
  - 回覆的 text 當成果、data 的引用整理成「資料來源」附在後面（網址轉回中文）；支援對方回 Message 或 Task
  - 對方沒用 LLM（llm_error）時提醒「可以打開共享開關」；401 提示檢查 token
  - 地圖：外出中半透明、名字加註「（外出中）」，狀態改變即時更新；派工時和村民一樣有工作與交接動畫
  - 實測：村子（開發模式）問「臺灣第一位民選總統是誰？」→ 村長派給台灣史專家 → 專家用共享的 qwen3.5 回答並附引用
- [x] 使用者部署實測（T4-A、T4-B）
- [x] 串流：使用者發現駐村專家的回答一次跳出來、不像其他村民逐字出現
  - 台灣史專家：LLM 改用串流（`chat_stream`，不支援串流的模型自動改一次拿完）；A2A 新增 `message/stream`（SSE：
    Task → artifact-update 文字片段 → artifact-update 引用 data → status-update completed）；agent card `capabilities.streaming: true`
  - 村子：呼叫駐村專家先試 `message/stream`、邊收邊顯示；對方回「不支援」（-32601）就改用 `message/send` 並記住；
    對方直接回完整 JSON 也照收

### T5：進階（之後再說）
- [ ] GraphRAG（人物、事件、地點的關係圖）或 agentic RAG（自己決定要查幾次、查什麼）
- [ ] 更多資料來源（政府開放資料、其他公開史料）

## 待討論

- **回答的風格**：長度、要不要附年表、引用的呈現方式
- **資料清單的維護**：之後要不要能在介面上新增條目、重新抓取
- **與村子的對接細節**：A2A 協定要做到多完整（串流、任務狀態）

## 資料授權

中文維基百科內容採 **CC BY-SA 4.0** 授權：可以使用與修改，但要**標示出處**（條目網址）並以**相同授權**分享衍生內容。
因此每篇文件都保留原始網址，回答時附上引用來源；`corpus/` 的文件沿用 CC BY-SA 4.0。
