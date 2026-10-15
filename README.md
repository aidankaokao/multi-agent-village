# Multi-Agent Village

像素風的多 agent 村落：每個 agent 是一位村民，村長（orchestrator）依 Agent Card 指派村民合作完成你的問題。

![sprites](docs/images/sprites_preview.png)

## 快速開始

需要：Python 3.12+ 與 [uv](https://docs.astral.sh/uv/)、Node 20+；本地模型可用 [Ollama](https://ollama.com)。

```bash
# 後端
cd backend && uv sync && uv run village serve      # http://127.0.0.1:8000

# 前端（另開終端機）
cd frontend && npm install && npm run dev            # http://localhost:5173
```

1. 打開 http://localhost:5173 → 「LLM 設定」
2. 新增 provider（Ollama 或雲端）→ 測試連線 → 註冊模型 → 指派 large / small
3. 回到「問答」按「播放示範」看村民動起來

## 容器部署

```bash
cp .env.example .env     # 填入 FRONTEND_PORT（必填），其餘可用預設
./build.sh               # 只 build image
docker-compose up -d     # 部署 → http://localhost:<FRONTEND_PORT>
```

- 只有前端（nginx）對外開 port，後端只在 compose 內部網路，`/api`、`/ws` 由 nginx 代理
- 前端綁 `0.0.0.0`，本機用 localhost、區網其他機器用主機 IP 都能連
- 容器內連主機 Ollama：`http://host.docker.internal:11434/v1`（已是預設值）
- 資料在 volume：`village-data`（DB）、`village-secrets`（主金鑰）。兩者分開，備份 DB 時記得另外妥善保存 secrets；**遺失主金鑰 = 已存的 API key 全部解不開**
- `./build.sh --no-cache` 不使用 build cache

## 安全性

- API key 以 AES-256-GCM 加密存入 DB；主金鑰在 `~/.config/multi-agent-village/master.key`（專案外，權限 600）或環境變數 `VILLAGE_MASTER_KEY(S)`
- API 永遠不回傳明文 key；log 與錯誤訊息自動遮罩
- 選用：在 `.env` 設 `VILLAGE_ADMIN_TOKEN` 後，LLM 設定頁需要輸入同一個值（預設不需要；網頁會被不信任的人連到時建議開啟）

詳見 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)、[docs/ROADMAP.md](docs/ROADMAP.md)。
