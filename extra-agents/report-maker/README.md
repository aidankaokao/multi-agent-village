# 文件彙整師（獨立 agent）

把上傳的文件（Excel `.xlsx`、`.csv`、`.txt`、`.pdf`，最多 3 份）整理成一份彙整報告 PDF：
總結、重點數字、各檔重點、跨檔比較、結論、建議；附錄是程式直接讀檔算出的欄位統計與分組小計（沒有經過 LLM）。

- 讀檔與計算都由程式做：欄位類型、總和 / 平均 / 最大 / 最小、依類別（部門、品項…）的分組小計；
  LLM 只負責文字整理，「重點數字」對不上原始資料的會被移除
- 文件太長時先分段摘要再彙整；txt / csv 自動判斷編碼（UTF-8、Big5）；掃描版 PDF 不支援（會直接告知）
- 報告用的 LLM 由呼叫端每次帶進來（header），agent 不存任何 key；沒有 LLM 時產生「統計版」報告
- 檔案只在記憶體處理，不存檔；容器根目錄唯讀、非 root 執行
- 規劃與進度：[PLAN.md](PLAN.md)

## 部署

```bash
cd extra-agents/report-maker
./build.sh            # 第一次會建立 .env 並自動產生存取 token（會印出來）
docker-compose up -d  # 自己執行
```

打開 http://localhost:8101 是測試網頁：

1. 展開「⚙️ 連線設定」，存取 token 填 `.env` 裡的 `RM_ACCESS_TOKENS`
2. LLM：按「填入 OpenRouter」再填模型與 key，或「填入本機 Ollama」（不填 LLM 也可以，會產生統計版報告）
3. 按「套用」→ 選檔案（或拖進來）→ 填重點方向（選填）→「產生報告」→ 下載 PDF

設定都在 `.env`（範本 `.env.example`）：port、存取 token、LLM 網址允許清單、上傳限制、同時處理數。

## API

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/` | 測試網頁 |
| GET | `/health` | 健康檢查（PDF 產生器能不能用、上傳限制） |
| GET | `/.well-known/agent-card.json` | A2A 自我介紹（收哪些檔案：`skills[].inputModes`、`x-file-limits`） |
| POST | `/a2a` | A2A JSON-RPC：`message/send`、`message/stream`（需要 `Authorization: Bearer <token>`） |

文件放在訊息的 file part（base64，不支援 uri）：

```json
{"jsonrpc": "2.0", "id": 1, "method": "message/stream", "params": {"message": {
  "kind": "message", "role": "user", "messageId": "…",
  "parts": [
    {"kind": "text", "text": "著重各部門的成本"},
    {"kind": "file", "file": {"name": "銷售.xlsx", "mimeType": "…", "bytes": "<base64>"}}
  ]}}}
```

回覆（`message/send` 的 Message parts，或 `message/stream` 的 artifact-update）：

- `text`：聊天視窗用的短版（標題、總結、結論、建議）
- `file`：報告 PDF（`application/pdf`，base64）
- `data`：標題、模型、耗時、提醒、讀不了的檔案、各檔基本資料

`message/stream` 會先用 `status-update`（`final: false`）送進度文字：「讀取檔案 2/3：…」「摘要「…」：1/4 段」「撰寫報告中」「產生 PDF」。

LLM 放在 header：`X-LLM-Base-URL`、`X-LLM-Model`、`X-LLM-API-Key`（本機 Ollama 可省略）、`X-LLM-Reasoning-Effort`（選填）。

## 本機開發

```bash
uv run pytest
uv run rmk read 檔案…                                   # 只讀檔，看程式整理出的內容
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib      # macOS：WeasyPrint 找 Homebrew 的 Pango
uv run rmk report 檔案… -q "重點方向" -o 報告.pdf --llm-url http://localhost:11434/v1 --llm-model qwen3.5:9b --effort none
uv run rmk serve                                        # 127.0.0.1:8101
```
