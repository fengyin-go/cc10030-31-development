"""租船合约领域服务：跨模块业务链路（合约→航次）校验。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class CharterService(BaseModuleService):
    MODULE = "charter"
    ENTITY = "合约单"
    OPERATION_TERM = "签认合约"
    LIST_FIELDS = ["租用船舶", "承租方", "日租金", "起租日期", "退租日期", "航区限制", "合约状态", "备注说明"]
    REQUIRED_FIELDS = ["租用船舶", "承租方", "日租金"]
    STATUS_ORDER = ["草稿", "待签认", "已签认", "已终止"]
    ACTION_RULES = {"签认合约": "已签认", "调整条款": "待签认", "终止合约": "已终止"}
    NEGATIVE_ACTIONS = []

    def run_action(
        self,
        entry_id: int,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        # 终止合约时级联检查：该船舶是否有进行中航次
        if action == "终止合约":
            entry = store.find(self.MODULE, entry_id)
            if entry:
                vessel_name = entry.get("租用船舶", "")
                for voyage_row in store.rows("voyage"):
                    if str(voyage_row.get("船舶编号", "")) == vessel_name:
                        if voyage_row.get("status") not in ("已完成",):
                            return None, f"终止合约失败：船舶 {vessel_name} 存在未完成航次 {voyage_row.get('id', '')}"
        return super().run_action(entry_id, action, context)
