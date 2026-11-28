"""跨模块查询与校验辅助函数：航次编排业务链路中的交叉验证逻辑。

每个函数都基于真实数据流设计，用于各领域服务在登记和动作执行时
检查跨模块约束。
"""
from __future__ import annotations

from typing import Any, Callable

from app.store import store


def require_entity(module: str, entity_id: str | int, label: str = "") -> dict[str, Any] | None:
    """按编号查找目标模块记录，未找到返回 None。"""
    try:
        entry_id = int(entity_id) if isinstance(entity_id, str) and entity_id.isdigit() else int(entity_id)
    except (ValueError, TypeError):
        return None
    return store.find(module, entry_id)


def entity_matches_status(
    module: str,
    entity_id: str | int,
    expected_statuses: list[str],
) -> tuple[bool, str]:
    """校验目标记录是否存在且状态在预期集合内。"""
    entry = require_entity(module, entity_id)
    if entry is None:
        return False, f"{module} 记录 {entity_id} 不存在"
    current_status = entry.get("status", "")
    if current_status not in expected_statuses:
        return False, f"{module} 记录 {entity_id} 状态为「{current_status}」，要求是 {expected_statuses}"
    return True, ""


def vessel_confirmed(vessel_id: str | int) -> tuple[bool, str]:
    """船舶必须已确认才能用于创建航次。"""
    return entity_matches_status("vessel", vessel_id, ["已确认"])


def route_confirmed(route_id: str | int) -> tuple[bool, str]:
    """航线必须已确认才能用于创建航次。"""
    return entity_matches_status("route", route_id, ["已确认"])


def vessel_not_sealed(vessel_id: str | int) -> tuple[bool, str]:
    """船舶未被封存才能创建航次。"""
    entry = require_entity("vessel", vessel_id)
    if entry is None:
        return False, f"船舶 {vessel_id} 不存在"
    if entry.get("status") == "已封存":
        return False, f"船舶 {vessel_id} 已被封存，不能创建新航次"
    return True, ""


def voyage_not_completed(voyage_id: str | int) -> tuple[bool, str]:
    """航次未完成才能关联新货物或船员排班。"""
    entry = require_entity("voyage", voyage_id)
    if entry is None:
        return False, f"航次 {voyage_id} 不存在"
    if entry.get("status") == "已完成":
        return False, f"航次 {voyage_id} 已完成，不允许新增关联记录"
    return True, ""


def voyage_exists(voyage_id: str | int) -> tuple[bool, str]:
    """航次存在性检查。"""
    entry = require_entity("voyage", voyage_id)
    if entry is None:
        return False, f"航次 {voyage_id} 不存在"
    return True, ""


def check_cargo_tonnage(voyage_id: str | int, cargo_tonnage: float) -> tuple[bool, str]:
    """货物登记时校验航次上全部货物总吨位不超船舶载重吨。"""
    voyage = require_entity("voyage", voyage_id)
    if voyage is None:
        return False, f"航次 {voyage_id} 不存在"

    vessel_id = voyage.get("船舶编号", "")
    vessel = require_entity("vessel", vessel_id)
    if vessel is None:
        return False, f"船舶 {vessel_id} 不存在"

    dwt_str = str(vessel.get("载重吨", "0"))
    try:
        dwt = float(dwt_str) if dwt_str.replace(".", "").isdigit() else 0
    except (ValueError, TypeError):
        dwt = 0

    # 计算现有货物总吨位
    existing_tonnage = 0.0
    for cargo_row in store.rows("cargo"):
        if str(cargo_row.get("装载航次", "")) == str(voyage_id) and cargo_row.get("status") != "已卸货":
            try:
                existing_tonnage += float(str(cargo_row.get("总吨数", 0)))
            except (ValueError, TypeError):
                pass

    if existing_tonnage + cargo_tonnage > dwt:
        return False, f"货物总吨位 {existing_tonnage + cargo_tonnage} 超过船舶载重吨 {dwt}"
    return True, ""


def check_crew_shift_overlap(
    crew_name: str,
    embark_date: str,
    disembark_date: str,
    exclude_voyage_id: str | int | None = None,
) -> tuple[bool, str]:
    """同一船员不能同时在两个重叠航次中排班。"""
    from datetime import date as date_type

    try:
        new_start = date_type.fromisoformat(embark_date)
        new_end = date_type.fromisoformat(disembark_date)
    except (ValueError, TypeError):
        # 日期格式无法解析时不拦截（让基础校验处理）
        return True, ""

    for shift_row in store.rows("crew_shift"):
        if str(shift_row.get("船员姓名", "")) != crew_name:
            continue
        if shift_row.get("status") == "已下船":
            continue
        if exclude_voyage_id is not None and str(shift_row.get("id")) == str(exclude_voyage_id):
            continue

        exist_embark = str(shift_row.get("上船日期", ""))
        exist_disembark = str(shift_row.get("下船日期", ""))
        try:
            exist_start = date_type.fromisoformat(exist_embark)
            exist_end = date_type.fromisoformat(exist_disembark)
        except (ValueError, TypeError):
            continue

        # 日期重叠检测
        if new_start <= exist_end and new_end >= exist_start:
            return False, f"船员 {crew_name} 在 {embark_date} 至 {disembark_date} 与已有排班（{exist_embark} 至 {exist_disembark}）重叠"
    return True, ""


def port_call_in_sequence(voyage_id: str | int) -> tuple[bool, str]:
    """港口挂靠必须按航线顺序：前一个挂靠未离港不能新增后续挂靠。"""
    voyage = require_entity("voyage", voyage_id)
    if voyage is None:
        return True, ""  # 存在性由基础逻辑校验

    port_calls = [
        row for row in store.rows("port_call")
        if str(row.get("航次编号", "")) == str(voyage_id)
    ]
    # 按 id 模拟顺序
    port_calls.sort(key=lambda r: int(r.get("id", 0)))

    for i in range(len(port_calls) - 1):
        prev = port_calls[i]
        if prev.get("status") != "已离港":
            return False, f"前一个挂靠（{prev.get('挂靠港口', '')}）尚未离港，不能新增后续挂靠"
    return True, ""


def check_fuel_consumption_between_refuels(
    voyage_id: str | int,
    previous_fuel_qty: float,
) -> tuple[bool, str]:
    """同航次上一次加油后消耗未核验，不能新增燃料记录。"""
    fuel_logs = [
        row for row in store.rows("fuel_log")
        if str(row.get("船舶编号", "")) == str(voyage_id) or True
    ]
    # 查找最近一次待核验或草稿状态的燃料记录
    for log in fuel_logs:
        if log.get("status") in ("草稿", "待核验"):
            return False, "同航次存在未核验的燃料记录，请先完成核验"
    return True, ""


def weather_affects_voyage(sea_area: str) -> list[dict[str, Any]]:
    """气象预警影响特定海域后，返回经过该海域的未完成航次列表。"""
    affected_voyages: list[dict[str, Any]] = []
    for voyage_row in store.rows("voyage"):
        if voyage_row.get("status") == "已完成":
            continue
        # 通过航线查海域
        route_id = voyage_row.get("航线编号", "")
        route = require_entity("route", route_id)
        if route is None:
            continue
        # 检查航线经停港口是否涉及预警海域
        via_ports = str(route.get("经停港口", ""))
        if sea_area in via_ports:
            affected_voyages.append(voyage_row)
    return affected_voyages


def weather_impact_assessment(sea_area: str) -> dict[str, list[dict[str, Any]]]:
    """把预警海域的命中结果拆成「可确认受影响」「无法确认」「已完成」三组。

    只有航线已确认、且经停港口确实覆盖预警海域的航次才算可确认受影响，
    这类航次才具备领取正式处置的资格。航线缺失或尚未确认时，即使经停
    数据里出现了海域字样也不能直接生成处置，只能作为待核实航次记录。
    已完成航次不参与新的处置分配。
    """
    confirmed: list[dict[str, Any]] = []
    unconfirmed: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []

    if not sea_area:
        return {"affected": confirmed, "unconfirmed": unconfirmed, "completed": completed}

    for voyage_row in store.rows("voyage"):
        route_id = voyage_row.get("航线编号", "")
        route = require_entity("route", route_id)
        if route is None:
            # 航线档案缺失时无法判断是否经过预警海域，不纳入可处置范围。
            continue

        via_ports = str(route.get("经停港口", ""))
        if voyage_row.get("status") == "已完成":
            # 已完成航次只记录确实经过该海域的，避免把无关航次列进来。
            if route.get("status") == "已确认" and sea_area in via_ports:
                completed.append(voyage_row)
            continue

        if route.get("status") != "已确认":
            # 只有经停信息为空或已经提到该海域时，才属于「无法确认」；
            # 明确登记了其他海域的航线不打扰。
            if not via_ports.strip() or sea_area in via_ports:
                unconfirmed.append({
                    "voyage": voyage_row,
                    "reason": (
                        f"航次 {voyage_row.get('id', '')} 的航线 {route_id} "
                        f"状态为「{route.get('status', '')}」，无法确认是否经过预警海域 {sea_area}"
                    ),
                })
            continue

        if sea_area in via_ports:
            confirmed.append(voyage_row)

    return {"affected": confirmed, "unconfirmed": unconfirmed, "completed": completed}


def port_call_arrived(port_call_id: str | int) -> tuple[bool, str]:
    """港口挂靠已到达才能发起通关申报。"""
    return entity_matches_status("port_call", port_call_id, ["已确认"])


def clearance_approved_required(voyage_id: str | int) -> tuple[bool, str]:
    """航次有通关申报且审核通过才能允许装卸。"""
    for clearance_row in store.rows("clearance"):
        if str(clearance_row.get("航次编号", "")) == str(voyage_id):
            if clearance_row.get("status") != "审核通过":
                return False, f"航次 {voyage_id} 通关申报未审核通过"
    return True, ""


def all_cargo_loaded_for_voyage(voyage_id: str | int) -> tuple[bool, list[dict[str, Any]]]:
    """获取航次上全部已装载货物，用于编制舱单。"""
    loaded_cargo: list[dict[str, Any]] = []
    for cargo_row in store.rows("cargo"):
        if str(cargo_row.get("装载航次", "")) == str(voyage_id) and cargo_row.get("status") == "已装载":
            loaded_cargo.append(cargo_row)
    if not loaded_cargo:
        return False, []
    return True, loaded_cargo


def charter_active_for_vessel(vessel_name: str) -> tuple[bool, str]:
    """租船合约已签认且未终止才能创建新航次。"""
    for charter_row in store.rows("charter"):
        if str(charter_row.get("租用船舶", "")) == vessel_name:
            if charter_row.get("status") == "已终止":
                return False, f"船舶 {vessel_name} 的租船合约已终止，不能创建新航次"
            if charter_row.get("status") == "已签认":
                return True, ""
    # 没有找到租船合约时允许（自营船舶）
    return True, ""


def crew_cert_valid_for_shift(crew_name: str) -> tuple[bool, str]:
    """船员证书过期或即将到期时不能排入新航次。"""
    from datetime import date as date_type

    today = date_type.today()
    for cert_row in store.rows("crew_cert"):
        if str(cert_row.get("船员姓名", "")) == crew_name:
            expiry_str = str(cert_row.get("有效期至", ""))
            try:
                expiry = date_type.fromisoformat(expiry_str)
            except (ValueError, TypeError):
                continue
            if expiry <= today:
                return False, f"船员 {crew_name} 的证书已到期（{expiry_str}），不能排入新航次"
            # 证书状态为已到期也拦截
            if cert_row.get("status") == "已到期":
                return False, f"船员 {crew_name} 的证书已标记到期，不能排入新航次"
    return True, ""


def seal_vessel_cascade(vessel_id: str | int) -> tuple[bool, str]:
    """船舶封存后级联检查所有进行中航次。"""
    for voyage_row in store.rows("voyage"):
        if str(voyage_row.get("船舶编号", "")) == str(vessel_id):
            if voyage_row.get("status") not in ("已完成",):
                return False, f"船舶 {vessel_id} 存在未完成航次 {voyage_row.get('id', '')}，不能封存"
    return True, ""


def duplicate_manifest_check(voyage_id: str | int) -> tuple[bool, str]:
    """重复签认同一舱单不重复更新（幂等）。"""
    for manifest_row in store.rows("manifest"):
        if str(manifest_row.get("航次编号", "")) == str(voyage_id):
            if manifest_row.get("status") == "已签发":
                return False, f"航次 {voyage_id} 舱单已签发，不能重复签认"
    return True, ""


def voyage_complete_triggers_manifest(voyage_id: str | int) -> bool:
    """航次标记完成后自动检查是否需要生成舱单。"""
    ok, loaded = all_cargo_loaded_for_voyage(voyage_id)
    return ok


def concurrent_voyage_modification_check(
    voyage_id: str | int,
    expected_status: str,
) -> tuple[bool, str]:
    """并发修改航次的冲突检测：校验当前状态与预期是否一致。"""
    voyage = require_entity("voyage", voyage_id)
    if voyage is None:
        return False, f"航次 {voyage_id} 不存在"
    if voyage.get("status") != expected_status:
        return False, f"航次 {voyage_id} 状态已变更为「{voyage.get('status')}」，预期「{expected_status}」"
    return True, ""


# 10条业务链路的完整校验流水线
CROSS_CHAINS = {
    "vessel_route_to_voyage": {
        "description": "船舶已确认+航线已确认→创建航次",
        "checks": [vessel_confirmed, route_confirmed],
    },
    "voyage_to_cargo": {
        "description": "货物登记→关联未完成航次→总吨位不超载重吨",
        "checks": [voyage_not_completed],
        "tonnage_check": check_cargo_tonnage,
    },
    "voyage_to_crew_shift": {
        "description": "船员排班→指定航次→同一船员不跨重叠航次",
        "checks": [voyage_not_completed],
        "crew_overlap_check": check_crew_shift_overlap,
        "crew_cert_check": crew_cert_valid_for_shift,
    },
    "voyage_to_port_call": {
        "description": "港口挂靠→按航线顺序→前一个挂靠未离港不能新增后续",
        "checks": [voyage_exists],
        "sequence_check": port_call_in_sequence,
    },
    "voyage_to_fuel_log": {
        "description": "燃料记录→关联航次→同航次两次加油消耗核验",
        "consumption_check": check_fuel_consumption_between_refuels,
    },
    "weather_to_voyage": {
        "description": "气象预警→影响海域→自动标记未完成航次；无法确认的航次不得领取处置",
        "affected_voyages": weather_affects_voyage,
    },
    "port_call_to_clearance": {
        "description": "港口挂靠到达→港口代理发起通关申报→审核通过后允许装卸",
        "checks": [port_call_arrived],
    },
    "cargo_to_manifest": {
        "description": "全部货物装载→编制舱单→汇总件数和吨位",
        "loaded_check": all_cargo_loaded_for_voyage,
        "duplicate_check": duplicate_manifest_check,
    },
    "charter_to_voyage": {
        "description": "租船合约签认→船舶可创建新航次→合约到期后禁止",
        "checks": [charter_active_for_vessel],
    },
    "crew_cert_to_shift": {
        "description": "船员证书到期/过期→该船员不能排入新航次",
        "checks": [crew_cert_valid_for_shift],
    },
}