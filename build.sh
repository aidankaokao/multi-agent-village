#!/usr/bin/env bash
# 一鍵 build image（部署請自行執行 docker-compose up -d）
#
#   ./build.sh              build backend 與 frontend image
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
  echo "已從 .env.example 建立 .env，請先檢查內容再重新執行。" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

missing=()
for v in COMPOSE_PROJECT_NAME IMAGE_TAG; do
  [[ -z "${!v:-}" ]] && missing+=("$v")
done
if (( ${#missing[@]} )); then
  echo "請在 .env 填入：${missing[*]}" >&2
  exit 1
fi

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
# build 不需要 port；FRONTEND_PORT 還沒填時給暫時值，避免 compose 檔驗證失敗
FRONTEND_PORT="${FRONTEND_PORT:-0}" "${COMPOSE[@]}" build $NO_CACHE

echo
echo "✅ build 完成："
docker images --format '   {{.Repository}}:{{.Tag}}  ({{.Size}})' \
  | grep -E "(${BACKEND_IMAGE:-village-backend}|${FRONTEND_IMAGE:-village-frontend}):${IMAGE_TAG}" || true
echo
echo "部署：${COMPOSE[*]} up -d"
