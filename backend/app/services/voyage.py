"""航次编排领域服务：跨模块业务链路（船舶+航线→航次、租船合约→航次、航次→货物/舱单）校验。"""
from __future__ import annotations

from typing import Any

from app.services.alert_workflow import (
    alert_lock,
    continue_sign_off,
    link_voyage,
    sign_off_prompt,
    submit_reassignment,
)
from app.services.base import BaseModuleService
from app.services.cross_module import (
    charter_active_for_vessel,
    route_confirmed,
    vessel_confirmed,
)
from app.store import store


class VoyageService(BaseModuleService):
    MODULE = "voyage"
    ENTITY = "航次"
    OPERATION_TERM = "签认航次"
    LIST_FIELDS = ["船舶编号", "航线编号", "计划离港", "计划到港", "实际离港", "实际到港", "航次状态", "备注说明"]
    REQUIRED_FIELDS = ["船舶编号", "航线编号", "计划离港"]
    STATUS_ORDER = ["草稿", "待签认", "已签认", "已完成"]
    ACTION_RULES = {
        "签认航次": "已签认",
        "继续签认": "已签认",
        "先办改派": "待签认",
        "推迟离港": "待签认",
        "标记完成": "已完成",
    }
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]] | tuple[dict[str, Any] | None, str]:
        # 基础必填校验
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing

        # 跨模块校验：船舶必须已确认
        vessel_id = values.get("船舶编号", "")
        ok, msg = vessel_confirmed(vessel_id)
        if not ok:
            return None, f"创建航次失败：{msg}"

        # 跨模块校验：航线必须已确认
        route_id = values.get("航线编号", "")
        ok, msg = route_confirmed(route_id)
        if not ok:
            return None, f"创建航次失败：{msg}"

        # 跨模块校验：租船合约必须有效
        ok, msg = charter_active_for_vessel(vessel_id)
        if not ok:
            return None, f"创建航次失败：{msg}"

        # 新航次可能让在办预警补正回执多出一个受影响航次。
        link_voyage(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        context = context or {}
        if action == "先办改派":
            return self._submit_reassignment(entry_id, context)
        if action == "继续签认":
            return self._continue_sign_off(entry_id, context)
        if action == "签认航次":
            return self._sign_off(entry_id, context)

        # 标记完成时触发舱单待签发
        result, msg = super().run_action(entry_id, action, context)
        if result is not None and action == "标记完成":
            from app.services.cross_module import voyage_complete_triggers_manifest
            voyage = store.find("voyage", entry_id)
            if voyage and voyage_complete_triggers_manifest(entry_id):
                msg = "航次已完成，请关注舱单编制"
        return result, msg

    def _alert_args(self, context: dict[str, Any]) -> dict[str, str]:
        return {
            "alert_ref": str(
                context.get("alert_ref") or context.get("预警编号") or ""
            ),
            "receipt_code": str(
                context.get("receipt_code")
                or context.get("回执编号")
                or context.get("凭据")
                or ""
            ),
            "note": str(context.get("note") or context.get("备注说明") or ""),
        }

    def _sign_off(
        self,
        entry_id: int,
        context: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        with alert_lock():
            voyage = store.find(self.MODULE, entry_id)
            if voyage is None:
                return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
            if voyage.get("status") == "已完成":
                return None, f"航次 {entry_id} 已完成，不再开放处置资格"

            args = self._alert_args(context)
            prompt = sign_off_prompt(voyage, args["alert_ref"])
            if prompt:
                return None, prompt
            result, msg = super().run_action(entry_id, "签认航次", context)
            return result, msg

    def _continue_sign_off(
        self,
        entry_id: int,
        context: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        with alert_lock():
            voyage = store.find(self.MODULE, entry_id)
            if voyage is None:
                return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
            args = self._alert_args(context)
            _disposition, msg = continue_sign_off(voyage, **args)
            if _disposition is None:
                return None, msg
            if _disposition.get("status") == "待改派":
                # 已领取先办改派、但改派尚未完成；此时不能把航次推进到已签认。
                return None, msg
            result, _base_msg = super().run_action(entry_id, "签认航次", context)
            return result, msg

    def _submit_reassignment(
        self,
        entry_id: int,
        context: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        with alert_lock():
            voyage = store.find(self.MODULE, entry_id)
            if voyage is None:
                return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
            args = self._alert_args(context)
            disposition, msg = submit_reassignment(entry_id, **args)
            if disposition is None:
                return None, msg
            # 改派提交后航次保持原状态，等待实际改派结果再签认。
            voyage["pending"] = True
            return voyage, msg
