#!/usr/bin/env bash
# 一鍵 build image 並部署容器
#
#   ./build.sh              build 全部 image 後 up -d
#   ./build.sh --no-cache   不使用 build cache
#   ./build.sh --build-only 只 build，不部署
set -euo pipefail

cd "$(dirname "$0")"

NO_CACHE=""
DEPLOY=1
for arg in "$@"; do
  case "$arg" in
    --no-cache) NO_CACHE="--no-cache" ;;
    --build-only) DEPLOY=0 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "未知參數：$arg" >&2; exit 1 ;;
  esac
done

# ---- 檢查 .env ----
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已從 .env.example 建立 .env，請先填好（至少 FRONTEND_PORT）再重新執行。" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

missing=()
for v in COMPOSE_PROJECT_NAME IMAGE_TAG FRONTEND_PORT; do
  [[ -z "${!v:-}" ]] && missing+=("$v")
done
if (( ${#missing[@]} )); then
  echo "請在 .env 填入：${missing[*]}" >&2
  exit 1
fi
if ! [[ "$FRONTEND_PORT" =~ ^[0-9]+$ ]] || (( FRONTEND_PORT < 1 || FRONTEND_PORT > 65535 )); then
  echo "FRONTEND_PORT 必須是 1-65535 的數字：$FRONTEND_PORT" >&2
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
echo "==> build images"
"${COMPOSE[@]}" build $NO_CACHE

if (( DEPLOY == 0 )); then
  echo "==> 已完成 build（未部署）"
  exit 0
fi

echo "==> 部署容器"
"${COMPOSE[@]}" up -d --remove-orphans

echo "==> 等待服務健康檢查"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
"${COMPOSE[@]}" ps

if curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/api/health" >/dev/null 2>&1; then
  echo
  echo "✅ 部署完成：http://${FRONTEND_BIND:-127.0.0.1}:${FRONTEND_PORT}"
  echo "   admin token：${COMPOSE[*]} exec backend cat /config/admin.token"
else
  echo
  echo "⚠️  服務尚未就緒，請查看 log：${COMPOSE[*]} logs -f" >&2
  exit 1
fi
