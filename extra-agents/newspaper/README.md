# 報社（newspaper agent）

把採訪資料（網路研究員查好的新聞、整理好的文字）編成一份 **A4 直式多頁的報紙 PDF**，附第一版的預覽圖。
獨立部署、用 A2A 呼叫；在 Multi-Agent Village 裡是「駐村專家」。

- 報頭（預設「村莊日報」，`.env` 的 `NP_MASTHEAD` 可改）、日期（臺灣時間，程式決定）、這期的名稱
- 頭條（大標、副標、導言、兩欄內文）→ 分版新聞（兩欄）→ 快訊 → 編輯室報告 → 資料來源（統一編號）
- 每則新聞標出處：內文上標數字對應最後的「資料來源」；資料裡找不到的數字列在「編輯台」說明
- **自己不上網找資料**：在村子裡由網路研究員先查（好幾個主題就各查一次），成果經中轉站交給報社

## 怎麼編

1. 讀採訪資料（txt / md，最多 6 份）：拆出每份最後的「資料來源」，來源重新編號（第一份 A1、A2…，第二份 B1…，避免撞號）
2. **記者**：每份資料各自請 LLM 寫成 1～4 則新聞＋快訊（JSON；好幾份同時寫，輸入輸出都小，本機小模型也做得到）
3. **總編輯**：看所有標題，決定頭條、這期的名稱、副標、編輯室報告（JSON，很短）
4. 程式組版：來源依出現順序統一編號、拿掉不存在的編號、核對數字 → WeasyPrint 排版 → pypdfium2 轉第一版預覽 PNG

任何一步失敗都有退路：那份資料照它的小標題排成新聞、第一則當頭條，一定出得了報。

寫稿用的 LLM 由呼叫端每次在 header 帶進來（`X-LLM-Base-URL`、`X-LLM-Model`、`X-LLM-API-Key`、`X-LLM-Reasoning-Effort`），
agent 不存任何 key；網址只能是允許清單裡的主機。

## 部署

```bash
cp .env.example .env      # 可以不改；token 留空 build.sh 會自動產生
./build.sh                # 只 build image
docker-compose up -d      # 測試網頁：http://localhost:8103
```

村子邀請駐村專家時：網址填 `http://host.docker.internal:8103`（村子在容器裡），token 填 `.env` 的 `NP_ACCESS_TOKENS`，
並打開「允許把提問者的 LLM 設定交給它」（不然只能排出沒經過 LLM 的版本）。

## 本機開發

```bash
uv sync
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib   # macOS：WeasyPrint 找 Pango（brew install pango）
uv run np make ai.txt stock.txt -q "科技與財經早報" -o paper.pdf --llm-url http://localhost:11434/v1 --llm-model qwen3.5:9b --effort none
uv run np serve           # http://127.0.0.1:8103
uv run pytest
```

本機 Ollama 的上下文預設 4096 token：資料很多時把 `NP_DOC_CHARS` 調低（例如 3000）。
