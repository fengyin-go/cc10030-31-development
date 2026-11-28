"""气象预警领域服务：跨模块业务链路（预警→海域→航次）与空态补正回执。"""
from __future__ import annotations

from typing import Any

from app.services.alert_workflow import (
    alert_label,
    recheck_alert,
    sign_alert,
)
from app.services.base import BaseModuleService
from app.services.cross_module import weather_impact_assessment
from app.store import store


class WeatherAlertService(BaseModuleService):
    MODULE = "weather_alert"
    ENTITY = "预警通知"
    OPERATION_TERM = "签收预警"
    LIST_FIELDS = [
        "预警海域", "预警类型", "预警等级", "生效时间", "预计持续", "影响航线", "签发单位", "备注说明",
        "补正回执", "回执状态", "受影响航次", "待核实航次",
    ]
    REQUIRED_FIELDS = ["预警海域", "预警类型", "预警等级"]
    STATUS_ORDER = ["草稿", "待签收", "已签收", "已解除"]
    ACTION_RULES = {
        "签收预警": "已签收",
        "调整航次": "已签收",
        "重新核对经停": "已签收",
        "解除预警": "已解除",
    }
    NEGATIVE_ACTIONS = []

    def run_action(
        self,
        entry_id: int,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        if action in ("签收预警", "重新核对经停"):
            return self._review_affected(entry_id, action)
        return super().run_action(entry_id, action, context)

    def _review_affected(
        self,
        entry_id: int,
        action: str,
    ) -> tuple[dict[str, Any] | None, str]:
        alert = store.find(self.MODULE, entry_id)
        if alert is None:
            return None, f"预警通知 {entry_id} 不存在或已归档"
        if action == "签收预警" and alert.get("status") not in ("草稿", "待签收"):
            return None, (
                f"预警 {alert_label(alert)} 状态为「{alert.get('status', '')}」，"
                f"不能重复签收；如需重新核对影响范围请用「重新核对经停」"
            )

        if action == "签收预警":
            alert["status"] = "已签收"
            alert["pending"] = False
            alert["abnormal"] = False
            receipt, affected = sign_alert(alert)
            if affected:
                ids = "、".join(str(row.get("id", "")) for row in affected)
                return alert, (
                    f"预警已签收，影响航次：{ids}；"
                    f"补正回执 {receipt.get('回执编号', '')} 已进入待处置"
                )
            assessment = weather_impact_assessment(str(alert.get("预警海域", "")))
            pending_ids = "、".join(
                str(item["voyage"].get("id", "")) for item in assessment["unconfirmed"]
            )
            detail = f"，待核实航次：{pending_ids}" if pending_ids else ""
            return alert, (
                f"预警已签收，暂未命中航线；已生成空态补正回执 "
                f"{receipt.get('回执编号', '')}（待核对）{detail}。"
                f"航线补报经停后可执行「重新核对经停」继续办理"
            )

        receipt, assessment = recheck_alert(alert)
        if assessment["affected"]:
            ids = "、".join(str(row.get("id", "")) for row in assessment["affected"])
            return alert, (
                f"已重新核对经停，受影响航次：{ids}；"
                f"回执 {receipt.get('回执编号', '')} 状态为「{receipt.get('status', '')}」"
            )
        pending_ids = "、".join(
            str(item["voyage"].get("id", "")) for item in assessment["unconfirmed"]
        )
        detail = f"，待核实航次：{pending_ids}" if pending_ids else ""
        return alert, (
            f"已重新核对经停，仍未确认受影响航次{detail}；"
            f"回执 {receipt.get('回执编号', '')} 保留在待核对，可在航线补报经停后再次核对"
        )
