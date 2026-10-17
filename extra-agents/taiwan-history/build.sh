#!/usr/bin/env bash
# 台灣史專家：只 build image（部署請自行執行 docker-compose up -d）
#
#   ./build.sh              build image
#   ./build.sh --no-cache   不使用 build cache
set -euo pipefail

cd "$(dirname "$0")"

NO_CACHE=""
for arg in "$@"; do
  case "$arg" in
    --no-cache) NO_CACHE="--no-cache" ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "未知參數：$arg" >&2; exit 1 ;;
  esac
done

# ---- 檢查 .env ----
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已從 .env.example 建立 .env。"
fi

# 還沒設定存取 token：自動產生一個寫進 .env
if ! grep -qE '^TH_ACCESS_TOKENS=.+' .env; then
  token=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
  if grep -q '^TH_ACCESS_TOKENS=' .env; then
    sed -i.bak "s|^TH_ACCESS_TOKENS=.*|TH_ACCESS_TOKENS=${token}|" .env && rm -f .env.bak
  else
    echo "TH_ACCESS_TOKENS=${token}" >> .env
  fi
  echo "已產生存取 token 並寫入 .env（TH_ACCESS_TOKENS）。測試網頁與呼叫端要用這個 token："
  echo "   ${token}"
fi
chmod 600 .env

set -a
# shellcheck disable=SC1091
source .env
set +a

# ---- docker compose 或 docker-compose ----
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "找不到 docker compose / docker-compose" >&2
  exit 1
fi

echo "==> 專案 ${COMPOSE_PROJECT_NAME}，版本 ${IMAGE_TAG}"
"${COMPOSE[@]}" build $NO_CACHE
docker image prune -f --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" >/dev/null

echo
echo "✅ build 完成：${IMAGE_REGISTRY}${IMAGE_NAME}:${IMAGE_TAG}"
echo
echo "部署：${COMPOSE[*]} up -d    → 打開 http://localhost:${TH_PORT}"
echo "（第一次啟動會自動建索引，約 7 分鐘；主機的 Ollama 要有 bge-m3：ollama pull bge-m3）"
