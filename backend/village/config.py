"""執行期設定，全部來自環境變數。

這裡只放「非機密」設定。LLM provider / 模型存在 DB（由 UI 註冊），
主金鑰與 admin token 放在 config_dir（預設 ~/.config/multi-agent-village，在專案資料夾外）。
"""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    database_url: str
    config_dir: Path
    agents_dir: Path = REPO_ROOT / "agents"
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Settings":
        default_db = REPO_ROOT / "data" / "village.db"
        return cls(
            database_url=os.environ.get("DATABASE_URL", f"sqlite+aiosqlite:///{default_db}"),
            config_dir=Path(
                os.environ.get("VILLAGE_CONFIG_DIR", Path.home() / ".config" / "multi-agent-village")
            ),
            agents_dir=Path(os.environ.get("VILLAGE_AGENTS_DIR", REPO_ROOT / "agents")),
            host=os.environ.get("VILLAGE_HOST", "127.0.0.1"),
            port=int(os.environ.get("VILLAGE_PORT", "8000")),
        )
