"""预警处置领域服务：受影响航次的改派/继续签认处置登记。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService


class AlertDispositionService(BaseModuleService):
    MODULE = "alert_disposition"
    ENTITY = "预警处置"
    OPERATION_TERM = "完成改派"
    LIST_FIELDS = [
        "处置编号", "回执编号", "预警编号", "航次编号", "处置类型",
        "处置说明", "更新时间", "备注说明",
    ]
    REQUIRED_FIELDS = ["回执编号", "航次编号", "处置类型"]
    STATUS_ORDER = ["待改派", "继续签认", "已改派", "已取消"]
    ACTION_RULES = {"完成改派": "已改派", "取消处置": "已取消"}
    NEGATIVE_ACTIONS = ["取消处置"]

    def run_action(
        self,
        entry_id: int,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = self.get_entry(entry_id)
        if entry is not None and action == "完成改派":
            if entry.get("处置类型") != "先办改派":
                return None, (
                    f"处置 {entry.get('处置编号', '')} 的类型是「{entry.get('处置类型', '')}」，"
                    f"不需要执行完成改派"
                )
            if entry.get("status") != "待改派":
                return entry, f"处置 {entry.get('处置编号', '')} 当前是「{entry.get('status', '')}」，无需重复完成改派"
        result, msg = super().run_action(entry_id, action, context)
        if result is None:
            return result, msg

        # 已改派 / 继续签认 / 已取消 都是终态，不再计入待处理。
        result["pending"] = False
        from app.services.alert_workflow import refresh_receipt, sync_alerts_for_receipt
        from app.store import store

        # 状态变了，回执和预警主档上的处置进度要同步刷新。
        for receipt in store.rows("weather_receipt"):
            if str(receipt.get("id", "")) == str(result.get("回执记录", "")):
                refresh_receipt(receipt)
                sync_alerts_for_receipt(receipt)
                msg = f"{msg}，补正回执 {receipt.get('回执编号', '')} 已更新"
                break
        return result, msg
