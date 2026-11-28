"""航线编排领域服务：经停更正经动预警补正回执的重新核对。"""
from __future__ import annotations

from typing import Any

from app.services.alert_workflow import alert_lock, link_route_correction
from app.services.base import BaseModuleService


class RouteService(BaseModuleService):
    MODULE = "route"
    ENTITY = "航线"
    OPERATION_TERM = "确认航线"
    LIST_FIELDS = ["起运港", "目的港", "经停港口", "标准航程", "航线等级", "适航船舶", "最近更新", "备注说明"]
    REQUIRED_FIELDS = ["起运港", "目的港", "经停港口"]
    STATUS_ORDER = ["草稿", "待确认", "已确认", "已停用"]
    ACTION_RULES = {
        "确认航线": "已确认",
        "调整经停": "已确认",
        "补报经停": "已确认",
        "停用航线": "已停用",
    }
    NEGATIVE_ACTIONS = ["停用航线"]

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]] | tuple[dict[str, Any] | None, str]:
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing
        # 登记时就带上了经停港口，等于一次补报；但航线尚未确认，
        # 只让回执重新核对，不改变「无法确认」的判断。
        link_route_correction(entry, source="经停补报")
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        if action not in ("确认航线", "调整经停", "补报经停"):
            return super().run_action(entry_id, action, context)

        with alert_lock():
            route = self.get_entry(entry_id)
            if route is None:
                return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
            new_ports = str((context or {}).get("经停港口") or "").strip()
            if new_ports:
                # 动作里可以带新的经停港口，先落库再刷新回执。
                route["经停港口"] = new_ports
            result, msg = super().run_action(entry_id, action, context)
            if result is None:
                return result, msg
            touched = link_route_correction(result, source="经停补报")
            if touched and result.get("status") == "已确认":
                codes = "、".join(str(row.get("回执编号", "")) for row in touched)
                return result, f"{msg}，已关联并重新核对预警补正回执：{codes}"
            return result, msg
