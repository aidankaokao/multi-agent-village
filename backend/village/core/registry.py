"""村民名冊：執行時從記憶體讀取，內容來自 DB（store/agents.py）。

agents/<id>/agent.yaml 是內建範例，只在 DB 還沒有該 id 時匯入一次（load_dir）。
"""

import logging
from pathlib import Path

from village.core.card import AgentSpec, load_spec

log = logging.getLogger("village.registry")

RESERVED_IDS = {"mayor", "craftsman", "reviewer", "scribe", "appraiser"}  # 村長、工匠、審核員、書記、鑑定師由系統提供


class AgentRegistry:
    def __init__(self, specs: list[AgentSpec] | None = None):
        self._specs: dict[str, AgentSpec] = {}
        self.errors: dict[str, str] = {}  # 村民 id / 資料夾名稱 -> 載入錯誤
        # 停用中的村民：id -> 「名字（說明）」。不會被派工，但村長要知道（使用者要的工作需要它時，告訴使用者它停用中）
        self.disabled: dict[str, str] = {}
        if specs:
            self.replace(specs)

    def replace(self, specs: list[AgentSpec], errors: dict[str, str] | None = None, disabled: dict[str, str] | None = None) -> None:
        self._specs = {s.id: s for s in specs}
        self.errors = errors or {}
        self.disabled = disabled or {}
        log.info("agents loaded: %s", sorted(self._specs))

    def all(self) -> list[AgentSpec]:
        return list(self._specs.values())

    def get(self, agent_id: str) -> AgentSpec | None:
        return self._specs.get(agent_id)

    def disabled_text(self) -> str:
        return "、".join(self.disabled.values())


def load_dir(root: Path) -> tuple[list[AgentSpec], dict[str, str]]:
    """讀取 root/*/agent.yaml；壞掉的檔案記在 errors，不影響其他村民。"""
    specs: list[AgentSpec] = []
    errors: dict[str, str] = {}
    if not root.is_dir():
        return specs, errors
    for path in sorted(root.glob("*/agent.yaml")):
        folder = path.parent.name
        try:
            spec = load_spec(path)
            if spec.id in RESERVED_IDS:
                raise ValueError(f"「{spec.id}」是保留名稱")
            specs.append(spec)
        except Exception as e:  # yaml 語法、欄位驗證等錯誤
            errors[folder] = str(e)
            log.warning("載入 agents/%s 失敗：%s", folder, e)
    return specs, errors
