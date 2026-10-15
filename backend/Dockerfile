# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.6.14

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim
COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python

# 先裝依賴（利用 layer cache），再放程式碼
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY village ./village
RUN uv sync --frozen --no-dev

# 非 root 執行；/data 放 DB、/config 放主金鑰與 admin token（分開兩個 volume）
RUN useradd --system --uid 10001 village \
    && mkdir -p /data /config \
    && chown village:village /data /config \
    && chmod 700 /config
USER village

ENV PATH=/app/.venv/bin:$PATH \
    VILLAGE_HOST=0.0.0.0 \
    VILLAGE_PORT=8000 \
    DATABASE_URL=sqlite+aiosqlite:////data/village.db \
    VILLAGE_CONFIG_DIR=/config \
    VILLAGE_AGENTS_DIR=/app/agents

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"

CMD ["village", "serve"]
