"""船舶档案领域服务：跨模块业务链路（船舶→航次→封存级联）校验。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.cross_module import seal_vessel_cascade


class VesselService(BaseModuleService):
    MODULE = "vessel"
    ENTITY = "船舶"
    OPERATION_TERM = "确认档案"
    LIST_FIELDS = ["船名", "总吨位", "载重吨", "建造年份", "船籍港", "船长姓名", "当前状态", "备注说明"]
    REQUIRED_FIELDS = ["船名", "总吨位", "载重吨"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已封存"]
    ACTION_RULES = {"确认档案": "已确认", "更新状态": "已确认", "封存船舶": "已封存"}
    NEGATIVE_ACTIONS = []

    def run_action(
        self,
        entry_id: int,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        # 封存船舶时级联检查所有进行中航次
        if action == "封存船舶":
            ok, msg = seal_vessel_cascade(entry_id)
            if not ok:
                return None, f"封存失败：{msg}"
        return super().run_action(entry_id, action, context)
