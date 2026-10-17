# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.6.14

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim
COPY --from=uv /uv /usr/local/bin/uv

# WeasyPrint 需要 Pango；報告用 Noto Sans CJK（沒有中文字型會變成方塊）
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python

# 先裝依賴（利用 layer cache），再放程式碼
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
RUN uv sync --frozen --no-dev

# 非 root 執行；上傳的檔案只在記憶體處理，不需要任何可寫入的資料夾
RUN useradd --system --uid 10001 rm
USER rm

# 容器根目錄唯讀（docker-compose 的 read_only）：快取寫到記憶體裡的 /tmp
ENV PATH=/app/.venv/bin:$PATH \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache

EXPOSE 8101
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8101/health', timeout=2)"

CMD ["rmk", "serve", "--host", "0.0.0.0", "--port", "8101"]
