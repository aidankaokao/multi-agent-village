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

# 先裝依賴（利用 layer cache），再放程式碼與資料文件
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY corpus ./corpus
RUN uv sync --frozen --no-dev

# 非 root 執行；/data 放索引（volume，可以從 corpus 重建）
RUN useradd --system --uid 10001 th \
    && mkdir -p /data \
    && chown th:th /data
USER th

ENV PATH=/app/.venv/bin:$PATH \
    TH_CORPUS_DIR=/app/corpus/docs \
    TH_DATA_DIR=/data

EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=2)"

CMD ["th", "serve", "--host", "0.0.0.0", "--port", "8100"]
