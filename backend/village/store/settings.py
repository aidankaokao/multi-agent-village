"""村規（全域設定）：存 DB 的 settings 表，在介面「村規」分頁調整，執行時從記憶體讀。"""

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import async_sessionmaker

from village.store.models import Setting

KEY = "village"


class VillageRules(BaseModel):
    reviewer_enabled: bool = True  # 最終答案送出前由審核員檢查
    reviewer_target: str = "tier:large"  # 'tier:large' / 'tier:small' / 模型 id
    review_redo: int = Field(default=1, ge=0, le=2)  # 不合格時退回重做的次數
    step_timeout_s: int = Field(default=180, ge=10, le=1800)  # 每一步的時間上限
    parallel: bool = True  # 互不依賴的步驟同時執行
    max_parallel: int = Field(default=3, ge=1, le=8)
    keep_runs: int = Field(default=500, ge=10, le=10_000)  # 執行紀錄保留筆數
    allow_registration: bool = True  # 登入頁可以自己註冊（一般成員）
    context_turns: int = Field(default=3, ge=0, le=10)  # 問答時帶入前幾輪對話（0 = 每次都是新問題）
    memory_enabled: bool = True  # 問答時帶入長期記憶
    memory_auto: bool = True  # 書記在問答後自動記錄
    memory_max: int = Field(default=10, ge=1, le=30)  # 每次最多帶入幾條記憶
    max_images: int = Field(default=3, ge=0, le=10)  # 問答每次最多附幾張圖（0 = 不開放附圖）
    paint_per_run: int = Field(default=1, ge=0, le=4)  # 畫家每次問答最多畫幾張（0 = 不開放畫圖）
    paint_per_day: int = Field(default=20, ge=0, le=500)  # 畫家每人每天（最近 24 小時）最多畫幾張
    loop_max_rounds: int = Field(default=3, ge=1, le=5)  # 迴圈（審查不通過就重做）最多幾輪；計畫寫的輪數不能超過它
    transcribe_max_minutes: int = Field(default=180, ge=0, le=600)  # 聽打員每份錄音最長幾分鐘（0 = 不開放轉寫）
    attachment_keep_days: int = Field(default=90, ge=0, le=3650)  # 使用者附的圖與文件保存幾天（0 = 跟著對話永久保存）


class RulesStore:
    def __init__(self, sessionmaker: async_sessionmaker):
        self._sm = sessionmaker
        self.current = VillageRules()

    async def load(self) -> VillageRules:
        async with self._sm() as s:
            row = await s.get(Setting, KEY)
            # 欄位增減時：舊的值保留、新欄位用預設
            self.current = VillageRules.model_validate(row.value) if row else VillageRules()
        return self.current

    async def save(self, rules: VillageRules) -> VillageRules:
        async with self._sm() as s:
            row = await s.get(Setting, KEY)
            if row is None:
                s.add(Setting(key=KEY, value=rules.model_dump()))
            else:
                row.value = rules.model_dump()
            await s.commit()
        self.current = rules
        return rules
