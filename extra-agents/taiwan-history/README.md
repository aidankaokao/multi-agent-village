# 台灣史專家（獨立 agent）

以公開資料（中文維基百科等）建立的台灣史問答 agent：檢索相關段落、由 LLM 整理回答，並附上引用來源。
獨立部署，有自己的簡易問答網頁可以單獨測試；完成後透過 HTTP（A2A 協定）接進 Multi-Agent Village 當作「外地來的村民」。

規劃與進度見 [PLAN.md](PLAN.md)。

## 資料

- 條目清單：`corpus/sources.yaml`（依時期分類，可增減）
- 整理好的文件：`corpus/docs/<時期>/<標題>.md`，開頭記錄來源網址、版本、抓取日期與授權
- 內容來自中文維基百科，授權 **CC BY-SA 4.0**：使用時標示出處（每篇的網址），衍生內容以相同授權分享

需要 Python 3.12+ 與 [uv](https://docs.astral.sh/uv/)：

```bash
cd extra-agents/taiwan-history
uv sync

uv run python scripts/fetch_wiki.py              # 抓還沒抓過的條目
uv run python scripts/fetch_wiki.py --refresh    # 全部重新抓（維基內容更新時）
uv run python scripts/fetch_wiki.py --only 霧社事件 二二八事件
uv run python scripts/corpus_stats.py            # 各時期篇數、字數與品質檢查

uv run pytest                                     # 測試
```

## 部署（獨立容器）

需要 Docker 與主機上的 Ollama（有 bge-m3：`ollama pull bge-m3`）。

```bash
cd extra-agents/taiwan-history
./build.sh               # 只 build；第一次會建立 .env 並自動產生存取 token（TH_ACCESS_TOKENS）
docker-compose up -d     # 部署 → http://localhost:8100
```

- 第一次啟動會自動建索引（約 7 分鐘），網頁上方會顯示進度；之後只處理有變動的文件
- 預設只開給本機（`TH_BIND=127.0.0.1`）：呼叫時 header 裡有 LLM 的 API key，跨機器要走 HTTPS 才安全
- 索引在 volume `th-data`，可以隨時從 `corpus/` 重建：`docker-compose exec taiwan-history th index --rebuild`

## 測試網頁

打開 http://localhost:8100 ：

1. 「連線設定」填存取 token（`.env` 的 `TH_ACCESS_TOKENS`）、LLM 網址、模型、API key，按「套用」
   - 本機 Ollama：網址 `http://host.docker.internal:11434/v1`、key 留空
   - 勾「記住設定」並設一組密碼：設定會用密碼加密後存在瀏覽器，下次輸入密碼解開
2. 輸入問題按「問答」；回答裡的 [編號] 可以點，會跳到對應的原文段落與維基網址
3. 「只看找到的資料」只檢索、不呼叫 LLM（不花錢），用來看檢索準不準

## API

公開：`GET /health`、`GET /.well-known/agent-card.json`（A2A 自我介紹）

需要 `Authorization: Bearer <存取 token>`：

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/search?q=…&k=8` | 只檢索 |
| POST | `/ask` | `{"question": "…", "top_k": 8}` → `{answer, citations, sources, model, llm_error, timings}` |
| POST | `/a2a` | A2A JSON-RPC 2.0，`method: "message/send"`；回覆的 Message 有 text（回答）與 data（引用）兩個 part |

回答用的 LLM 由呼叫端每次放在 header（agent 用完就丟，不保存）：

| Header | 說明 |
|---|---|
| `X-LLM-Base-URL` | OpenAI 相容 API 網址，必須在 `TH_ALLOWED_LLM_HOSTS` 允許清單裡 |
| `X-LLM-Model` | 模型名稱 |
| `X-LLM-API-Key` | API key（本機 Ollama 可省略） |
| `X-LLM-Reasoning-Effort` | 選填：none / low / medium / high |

沒帶 LLM header 時只回傳找到的資料。

```bash
curl -s http://localhost:8100/ask \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "X-LLM-Base-URL: https://openrouter.ai/api/v1" -H "X-LLM-Model: openai/gpt-5-mini" -H "X-LLM-API-Key: $OPENROUTER_KEY" \
  -d '{"question": "霧社事件的起因是什麼？"}'
```
