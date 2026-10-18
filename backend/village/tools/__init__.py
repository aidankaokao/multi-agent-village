"""工具箱：工具型村民可以使用的工具（id → Tool）。新增工具時在這裡註冊。"""

from village.tools.base import Attachment, Tool, ToolError, ToolResult
from village.tools.calculator import Calculator
from village.tools.chart import Chart
from village.tools.clock import Clock
from village.tools.painter import Painter
from village.tools.typist import Typist

TOOLS: dict[str, Tool] = {t.id: t for t in (Calculator(), Chart(), Clock(), Painter(), Typist())}

__all__ = ["TOOLS", "Attachment", "Tool", "ToolError", "ToolResult"]
