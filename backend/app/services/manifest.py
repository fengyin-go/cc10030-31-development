"""舱单编制领域服务：跨模块业务链路（货物→舱单→幂等）校验。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.cross_module import (
    all_cargo_loaded_for_voyage,
    duplicate_manifest_check,
    voyage_exists,
)


class ManifestService(BaseModuleService):
    MODULE = "manifest"
    ENTITY = "舱单"
    OPERATION_TERM = "签发舱单"
    LIST_FIELDS = ["航次编号", "货物清单", "托运方汇总", "件数合计", "吨位合计", "编制日期", "签发人员", "备注说明"]
    REQUIRED_FIELDS = ["航次编号", "货物清单", "托运方汇总"]
    STATUS_ORDER = ["草稿", "待签发", "已签发", "已归档"]
    ACTION_RULES = {"签发舱单": "已签发", "退回修正": "草稿", "归档舱单": "已归档"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]] | tuple[dict[str, Any] | None, str]:
        # 基础必填校验
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing

        # 跨模块校验：航次必须存在
        voyage_id = values.get("航次编号", "")
        if voyage_id:
            ok, msg = voyage_exists(voyage_id)
            if not ok:
                return None, f"舱单编制失败：{msg}"

        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        # 幂等校验：重复签认同一舱单不重复更新
        if action == "签发舱单":
            entry = self.get_entry(entry_id)
            if entry:
                voyage_id = entry.get("航次编号", "")
                if voyage_id:
                    ok, msg = duplicate_manifest_check(voyage_id)
                    if not ok:
                        # 检查当前舱单是否已经是已签发
                        if entry.get("status") == "已签发":
                            return entry, "舱单已签发，无需重复操作"
                        return None, f"签发失败：{msg}"

                # 校验货物是否全部装载
                if voyage_id:
                    has_loaded, loaded_cargo = all_cargo_loaded_for_voyage(voyage_id)
                    if not has_loaded:
                        return None, f"签发失败：航次 {voyage_id} 没有已装载货物"
        return super().run_action(entry_id, action, context)
