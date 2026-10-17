"""設定：全部從環境變數（.env）讀取。這裡只有 agent 自己的設定；回答用的 LLM 由呼叫端每次帶進來，不存在這裡。"""

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 呼叫端可以指定的 LLM 網址（避免 agent 被拿去連任意網址、探測內網）
DEFAULT_ALLOWED_LLM_HOSTS = ["openrouter.ai", "api.openai.com", "localhost", "127.0.0.1", "host.docker.internal"]


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    return [x.strip() for x in raw.split(",") if x.strip()] if raw else list(default)


@dataclass
class Settings:
    corpus_dir: Path = field(default_factory=lambda: Path(os.environ.get("TH_CORPUS_DIR", ROOT / "corpus" / "docs")))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("TH_DATA_DIR", ROOT / "data")))
    # embedding 固定用本地 Ollama 的 bge-m3：資料與問題必須用同一個模型
    ollama_url: str = field(default_factory=lambda: os.environ.get("TH_OLLAMA_URL", "http://localhost:11434"))
    embed_model: str = field(default_factory=lambda: os.environ.get("TH_EMBED_MODEL", "bge-m3"))
    allowed_llm_hosts: list[str] = field(default_factory=lambda: _list("TH_ALLOWED_LLM_HOSTS", DEFAULT_ALLOWED_LLM_HOSTS))
    top_k: int = field(default_factory=lambda: int(os.environ.get("TH_TOP_K", "8")))

    @property
    def index_path(self) -> Path:
        return self.data_dir / "index.sqlite3"
