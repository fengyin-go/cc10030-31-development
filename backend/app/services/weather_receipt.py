"""预警补正回执领域服务：空态回执的待办查询与人工关闭。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class WeatherReceiptService(BaseModuleService):
    MODULE = "weather_receipt"
    ENTITY = "补正回执"
    OPERATION_TERM = "重新核对"
    LIST_FIELDS = [
        "回执编号", "预警编号", "预警海域", "预警等级", "回执类型", "触发方式",
        "受影响航次", "待核实航次", "已完成航次", "已处置航次", "处置明细",
        "核对时间", "补报时间", "核对说明", "备注说明",
    ]
    REQUIRED_FIELDS = ["回执编号", "预警编号"]
    STATUS_ORDER = ["待核对", "待处置", "已处置", "已关闭"]
    ACTION_RULES = {"重新核对": "待核对", "关闭回执": "已关闭"}
    NEGATIVE_ACTIONS = []

    def run_action(
        self,
        entry_id: int,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        if action == "重新核对":
            return self._recheck(entry_id)
        return super().run_action(entry_id, action, context)

    def _recheck(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        from app.services.alert_workflow import refresh_receipt, sync_alert_fields

        receipt = store.find(self.MODULE, entry_id)
        if receipt is None:
            return None, f"补正回执 {entry_id} 不存在或已归档"
        if receipt.get("status") == "已关闭":
            return None, (
                f"补正回执 {receipt.get('回执编号', '')} 已关闭，不能继续核对；"
                f"如仍有未确认的受影响航次，请先在气象预警上重新签收或补报经停"
            )
        alert = self._find_alert(receipt)
        if alert is None:
            return None, f"补正回执 {receipt.get('回执编号', '')} 关联的预警已不存在"

        assessment = refresh_receipt(
            receipt, source="回执重新核对", note="调度员从补正回执重新核对受影响经停")
        sync_alert_fields(alert, receipt)
        if assessment["affected"]:
            ids = "、".join(str(row.get("id", "")) for row in assessment["affected"])
            return receipt, (
                f"已重新核对，受影响航次：{ids}；回执状态「{receipt.get('status', '')}」"
            )
        unconfirmed = "、".join(
            str(item["voyage"].get("id", "")) for item in assessment["unconfirmed"])
        detail = f"，待核实航次：{unconfirmed}" if unconfirmed else ""
        return receipt, (
            f"已重新核对，仍未确认受影响航次{detail}；"
            f"请等航线补报经停后再次核对，或关闭本次空态回执"
        )

    def _find_alert(self, receipt: dict[str, Any]) -> dict[str, Any] | None:
        for key in ("预警记录", "预警编号"):
            value = str(receipt.get(key, "") or "").strip()
            if not value:
                continue
            if value.isdigit():
                alert = store.find("weather_alert", int(value))
                if alert is not None:
                    return alert
            for row in store.rows("weather_alert"):
                if value in {
                    str(row.get("id", "")),
                    f"W{row.get('id', '')}",
                    str(row.get("预警编号", "") or ""),
                }:
                    return row
        return None
