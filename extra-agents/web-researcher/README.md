# 網路研究員（獨立 agent）

搜尋網路、閱讀網頁後回答問題，每個說法附上出處（網址、網站、日期）。適合需要最新或即時資訊的問題。

- 搜尋引擎是自架的 [SearXNG](https://docs.searxng.org/)（和研究員一起跑、不對外開放），不需要任何付費 key
- 回答用的 LLM 由呼叫端每次帶進來（header），agent 不存任何 key；沒有 LLM 時只列出找到的資料
- 只抓公開網址（擋內網）、網頁內容當資料不當指令
- 規劃與進度：[PLAN.md](PLAN.md)

## 部署

```bash
cd extra-agents/web-researcher
./build.sh            # 第一次會建立 .env，自動產生存取 token（會印出來）與 SearXNG 金鑰，並下載 SearXNG
docker-compose up -d  # 自己執行
```

打開 http://localhost:8102 是測試網頁：展開「⚙️ 連線設定」，存取 token 填 `.env` 裡的 `WR_ACCESS_TOKENS`，
LLM 填 OpenRouter（或本機 Ollama）→「套用」→ 問問題。「只看搜尋結果」不讀網頁、不呼叫 LLM。

## 接進村子

右上 ⚙️ →「村民」→「🧳 邀請駐村專家」：網址 `http://host.docker.internal:8102`、存取 token 同上；
要讓它整理回答，勾「允許把提問者的 LLM 設定交給它」。

## API

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/` | 測試網頁 |
| GET | `/health` | 健康檢查（搜尋引擎連得上嗎） |
| GET | `/.well-known/agent-card.json` | A2A 自我介紹 |
| GET | `/search?q=` | 只搜尋（需要 token） |
| POST | `/ask` | `{question}` → 回答、引用、搜尋關鍵字（需要 token） |
| POST | `/a2a` | A2A JSON-RPC：`message/send`、`message/stream`（進度＋逐字回答） |

LLM 放在 header：`X-LLM-Base-URL`、`X-LLM-Model`、`X-LLM-API-Key`（本機 Ollama 可省略）、`X-LLM-Reasoning-Effort`（選填）。

## 本機開發

```bash
docker run -d --name searxng -p 127.0.0.1:8888:8080 -v "$PWD/searxng/settings.yml:/etc/searxng/settings.yml:ro" \
  -e SEARXNG_SECRET=dev searxng/searxng:2026.10.17-1c10abe2d
uv run pytest
uv run wr search 2026 台灣電價
uv run wr ask 2026 年台灣電價有調整嗎？ --llm-url http://localhost:11434/v1 --llm-model qwen3.5:9b --effort none
uv run wr serve        # 127.0.0.1:8102
```

本機 Ollama 的上下文預設只有 4096 token，資料多時回答會被截斷（研究員會自動減少資料重寫）。
想要完整一點：Ollama 設環境變數 `OLLAMA_CONTEXT_LENGTH=8192` 後重啟。
