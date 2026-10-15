"""Agent 市集：掃描 agents/*/agent.yaml，檔案有變動就重新載入（hot reload）。"""

import logging
from pathlib import Path

from village.core.card import AgentSpec, load_spec

log = logging.getLogger("village.registry")

RESERVED_IDS = {"mayor"}  # 村長由系統提供


class AgentRegistry:
    def __init__(self, root: Path):
        self.root = root
        self._specs: dict[str, AgentSpec] = {}
        self._fingerprint: dict[Path, float] | None = None
        self.errors: dict[str, str] = {}  # 資料夾名稱 -> 載入錯誤

    def _scan(self) -> dict[Path, float]:
        if not self.root.is_dir():
            return {}
        return {p: p.stat().st_mtime for p in sorted(self.root.glob("*/agent.yaml"))}

    def refresh(self) -> bool:
        """有變動時重新載入並回傳 True。"""
        fp = self._scan()
        if fp == self._fingerprint:
            return False
        specs: dict[str, AgentSpec] = {}
        errors: dict[str, str] = {}
        for path in fp:
            folder = path.parent.name
            try:
                spec = load_spec(path)
                if spec.id in RESERVED_IDS:
                    raise ValueError(f"「{spec.id}」是保留名稱")
                specs[spec.id] = spec
            except Exception as e:  # yaml 語法、欄位驗證等錯誤都不該讓整個市集掛掉
                errors[folder] = str(e)
                log.warning("載入 agents/%s 失敗：%s", folder, e)
        self._specs, self.errors, self._fingerprint = specs, errors, fp
        log.info("agents loaded: %s", sorted(specs))
        return True

    def all(self) -> list[AgentSpec]:
        return list(self._specs.values())

    def get(self, agent_id: str) -> AgentSpec | None:
        return self._specs.get(agent_id)
