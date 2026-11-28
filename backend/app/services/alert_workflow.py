"""气象预警补正工作流：空态回执、经停补报、处置领取与航次签认互斥。

这一层把「预警签收 → 受影响经停核对 → 补正回执 → 航次改派/继续签认」
串成一条可重入、可并发的业务链路。路由服务和航次服务只负责调用这里的
函数，不在路由层写业务判断。

关键约束：
- 预警签收后总会留下补正回执；签收时没有命中航线的，回执处于「待核对」，
  调度员可以随时「重新核对经停」继续办理，不会只剩一条普通回执。
- 航线新增/更正经停后，回执会重新核对受影响航次（补报与签认并发时由
  同一把锁串行化，后执行的一方总能看到前一方的最新状态）。
- 只有能确认受影响的航次才能领取正式处置；无法确认的只能继续核对。
- 同一次补正（同一份回执）对同一航次只保留一份处置，重复提交不会新增。
- 已完成航次不再开放处置资格。
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Any

from app.services.cross_module import require_entity, weather_impact_assessment
from app.store import store

RECEIPT_MODULE = "weather_receipt"
DISPOSITION_MODULE = "alert_disposition"

OPEN_RECEIPT_STATUSES = ("待核对", "待处置")

# 预警补报与航次签认可能同时到达。所有会改变受影响关系或处置状态的操
# 作都走同一把可重入锁，保证「补报 → 回执更新 → 签认判断」不会读到半成品。
ALERT_LOCK = threading.RLock()


def alert_lock() -> threading.RLock:
    """跨服务操作统一从这里取锁，保证读写同一临界区。"""
    return ALERT_LOCK


def _today() -> str:
    return date.today().isoformat()


def _next_id(module: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(module)), default=0) + 1


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _ids_text(rows: list[dict[str, Any]]) -> str:
    return "、".join(str(row.get("id", "")) for row in rows)


# ---------------------------------------------------------------------------
# 预警标识与回执编号
# ---------------------------------------------------------------------------

def alert_refs(alert: dict[str, Any]) -> set[str]:
    """预警可能被引用的全部写法：内部编号、业务编号、W+编号。"""
    refs: set[str] = set()
    alert_id = _text(alert.get("id"))
    if alert_id:
        refs.add(alert_id)
        refs.add(f"W{alert_id}")
    for key in ("预警编号", "预警代码", "编号"):
        value = _text(alert.get(key))
        if value:
            refs.add(value)
            refs.add(f"W{value}" if value.isdigit() else value)
    return refs


def alert_label(alert: dict[str, Any]) -> str:
    """给人看的预警编号：优先业务编号，其次 W+内部编号。"""
    for key in ("预警编号", "预警代码"):
        value = _text(alert.get(key))
        if value:
            return value if value.upper().startswith("W") else f"W{value}"
    alert_id = _text(alert.get("id"))
    return f"W{alert_id}" if alert_id else "预警"


def _receipt_refs(receipt: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for key in ("回执编号", "预警编号", "预警记录"):
        value = _text(receipt.get(key))
        if value:
            refs.add(value)
    return refs


def _alerts_matching(ref: str) -> list[dict[str, Any]]:
    if not ref:
        return []
    return [row for row in store.rows("weather_alert") if ref in alert_refs(row)]


def _alert_by_ref(ref: str) -> dict[str, Any] | None:
    matches = _alerts_matching(ref)
    if not matches:
        return None
    return max(matches, key=lambda row: int(row.get("id", 0)))


def _receipts_for_alert(alert: dict[str, Any]) -> list[dict[str, Any]]:
    refs = alert_refs(alert)
    return [
        row for row in store.rows(RECEIPT_MODULE)
        if {_text(row.get("预警编号")), _text(row.get("预警记录"))} & refs
    ]


def _open_receipts() -> list[dict[str, Any]]:
    return [
        row for row in store.rows(RECEIPT_MODULE)
        if row.get("status") in OPEN_RECEIPT_STATUSES
    ]


def _active_receipts() -> list[dict[str, Any]]:
    """所有还能被补报影响的回执：未关闭即可，含已处置（补报可能重新打开）。"""
    return [
        row for row in store.rows(RECEIPT_MODULE)
        if row.get("status") != "已关闭"
    ]


def _dispositions_for(receipt_id: str | int) -> list[dict[str, Any]]:
    return [
        row for row in store.rows(DISPOSITION_MODULE)
        if _text(row.get("回执记录")) == str(receipt_id)
    ]


def _disposition_for(receipt_id: str | int, voyage_id: str | int) -> dict[str, Any] | None:
    for row in _dispositions_for(receipt_id):
        if _text(row.get("航次编号")) == str(voyage_id):
            return row
    return None


def _disposition_summary(receipt: dict[str, Any]) -> str:
    parts: list[str] = []
    for row in _dispositions_for(receipt.get("id", "")):
        parts.append(
            f"航次{row.get('航次编号', '')}：{row.get('处置类型', '')}（{row.get('status', '')}）"
        )
    return "；".join(parts)


def _handled_voyage_ids(receipt: dict[str, Any]) -> list[str]:
    return [
        _text(row.get("航次编号"))
        for row in _dispositions_for(receipt.get("id", ""))
        if row.get("status") != "已取消"
    ]


# ---------------------------------------------------------------------------
# 回执生成与重新核对
# ---------------------------------------------------------------------------

def _receipt_status_after_refresh(
    receipt: dict[str, Any],
    affected: list[dict[str, Any]],
) -> str:
    """按受影响航次与处置结果推进回执状态。"""
    current = receipt.get("status", "")
    if current == "已关闭":
        return current
    dispositions = {
        _text(row.get("航次编号")): row
        for row in _dispositions_for(receipt.get("id", ""))
        if row.get("status") != "已取消"
    }
    if affected:
        all_settled = all(
            _text(voyage.get("id")) in dispositions
            and _settled(dispositions[_text(voyage.get("id"))])
            for voyage in affected
        )
        return "已处置" if all_settled else "待处置"
    if dispositions:
        # 受影响清单空了（例如航次已完成），但仍有未完成的改派，
        # 回执保持待处置，避免把在办处置显示成已办结。
        if any(not _settled(row) for row in dispositions.values()):
            return "待处置"
        return "已处置"
    return "待核对"


def refresh_receipt(
    receipt: dict[str, Any],
    *,
    source: str | None = None,
    note: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """重新核对回执对应海域，并同步受影响/待核实/处置展示字段。"""
    alert = None
    for ref in (_text(receipt.get("预警记录")), _text(receipt.get("预警编号"))):
        alert = _alert_by_ref(ref)
        if alert is not None:
            break

    sea_area = _text(receipt.get("预警海域") or (alert or {}).get("预警海域"))
    if alert:
        # 预警海域以预警主档为准，避免回执被手工改错后失联。
        receipt["预警海域"] = alert.get("预警海域", sea_area)
        receipt["预警等级"] = alert.get("预警等级", receipt.get("预警等级", ""))
        tight = _text(alert.get("预警编号") or alert.get("预警代码"))
        if tight:
            receipt["预警编号"] = alert_label(alert)
        receipt["预警记录"] = str(alert.get("id", receipt.get("预警记录", "")))
        sea_area = _text(alert.get("预警海域", sea_area))

    assessment = weather_impact_assessment(sea_area)
    affected = assessment["affected"]
    unconfirmed = assessment["unconfirmed"]

    receipt["受影响航次"] = _ids_text(affected)
    receipt["待核实航次"] = _ids_text([item["voyage"] for item in unconfirmed])
    receipt["已完成航次"] = _ids_text(assessment["completed"])
    receipt["已处置航次"] = "、".join(_handled_voyage_ids(receipt))
    receipt["处置明细"] = _disposition_summary(receipt)
    receipt["核对时间"] = _today()
    if source:
        receipt["触发方式"] = source
        receipt["补报时间"] = _today()
    if note:
        receipt["核对说明"] = note
    elif unconfirmed:
        receipt["核对说明"] = "；".join(item["reason"] for item in unconfirmed)

    receipt["status"] = _receipt_status_after_refresh(receipt, affected)
    receipt["pending"] = receipt["status"] in OPEN_RECEIPT_STATUSES
    receipt["abnormal"] = receipt["status"] == "待核对"
    return assessment


def ensure_receipt(
    alert: dict[str, Any],
    *,
    source: str = "签收核对",
    note: str = "",
) -> tuple[dict[str, Any], bool]:
    """取得预警的补正回执；已存在则复用，保证一次预警只有一份在办回执。"""
    existing = _receipts_for_alert(alert)
    # 已处置/已关闭的回执不再复用，后续补报会新开一份补正。
    for row in sorted(existing, key=lambda item: int(item.get("id", 0)), reverse=True):
        if row.get("status") in OPEN_RECEIPT_STATUSES:
            return row, False

    alert_id = str(alert.get("id", ""))
    label = alert_label(alert)
    seq = 1 + sum(
        1 for row in store.rows(RECEIPT_MODULE)
        if label in _receipt_refs(row)
    )
    receipt: dict[str, Any] = {
        "id": _next_id(RECEIPT_MODULE),
        "status": "待核对",
        "pending": True,
        "abnormal": source == "签收空结果",
        "回执编号": f"{label}-B{seq}",
        "预警编号": label,
        "预警记录": alert_id,
        "预警海域": alert.get("预警海域", ""),
        "预警等级": alert.get("预警等级", ""),
        "回执类型": "空态补正" if source == "签收空结果" else "影响核对",
        "触发方式": source,
        "受影响航次": "",
        "待核实航次": "",
        "已完成航次": "",
        "已处置航次": "",
        "处置明细": "",
        "核对时间": "",
        "补报时间": "",
        "核对说明": note,
        "备注说明": "",
    }
    store.rows(RECEIPT_MODULE).append(receipt)
    return receipt, True


def sync_alert_fields(alert: dict[str, Any], receipt: dict[str, Any]) -> None:
    """把回执的关键状态回写到预警主档，便于列表/详情直接看到办理入口。"""
    alert["补正回执"] = receipt.get("回执编号", "")
    alert["回执编号"] = receipt.get("回执编号", "")
    alert["回执状态"] = receipt.get("status", "")
    alert["受影响航次"] = receipt.get("受影响航次", "")
    alert["待核实航次"] = receipt.get("待核实航次", "")
    alert["补正回执记录"] = str(receipt.get("id", ""))


def sync_alerts_for_receipt(receipt: dict[str, Any]) -> None:
    """把回执状态回写到它关联的预警主档。"""
    for alert in _alerts_matching(_text(receipt.get("预警编号"))):
        sync_alert_fields(alert, receipt)


def sign_alert(alert: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """签收预警并建立补正回执；返回回执和当前可确认的受影响航次。"""
    with ALERT_LOCK:
        assessment = weather_impact_assessment(_text(alert.get("预警海域")))
        source = "签收空结果" if not assessment["affected"] else "签收核对"
        receipt, _ = ensure_receipt(alert, source=source)
        refresh_receipt(receipt, source=source)
        receipt["回执类型"] = "空态补正" if not assessment["affected"] else "影响核对"
        sync_alert_fields(alert, receipt)
        return receipt, assessment["affected"]


def recheck_alert(
    alert: dict[str, Any],
    *,
    source: str = "重新核对经停",
    note: str = "调度员重新核对受影响经停",
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """在预警上重新核对受影响经停；空态也会生成/复用可继续办理的待办回执。"""
    with ALERT_LOCK:
        receipt, _ = ensure_receipt(alert, source=source)
        assessment = refresh_receipt(receipt, source=source, note=note)
        sync_alert_fields(alert, receipt)
        return receipt, assessment


def link_route_correction(
    route: dict[str, Any],
    *,
    source: str = "经停补报",
) -> list[dict[str, Any]]:
    """航线新增或更正经停后，把相关预警回执重新关联并刷新受影响航次。

    只要回执海域出现在航线经停里就刷新；即使本次没有新增命中，也更新
    核对时间，方便调度员确认补报已经被系统看到。
    """
    via_ports = _text(route.get("经停港口"))
    if not via_ports:
        return []
    route_id = str(route.get("id", ""))
    touched: list[dict[str, Any]] = []
    with ALERT_LOCK:
        for receipt in _active_receipts():
            sea_area = _text(receipt.get("预警海域"))
            if not sea_area:
                continue
            # 两种情况都要刷新：这条航线可能让回执新出现受影响航次；
            # 这条航线自己的航次也可能不再经过预警海域，需要重新核对。
            touched_ids = {
                part.strip() for part in _text(receipt.get("受影响航次")).split("、")
                if part.strip()
            }
            receipt_route_ids = {
                str(voyage.get("航线编号", ""))
                for voyage in store.rows("voyage")
                if str(voyage.get("id", "")) in touched_ids
            }
            if sea_area not in via_ports and route_id not in receipt_route_ids:
                continue
            refresh_receipt(
                receipt,
                source=source,
                note=(
                    f"航线 {route.get('id', '')} 补报经停「{via_ports}」，"
                    f"已重新核对受影响航次"
                ),
            )
            for alert in _alerts_matching(_text(receipt.get("预警编号"))):
                sync_alert_fields(alert, receipt)
            touched.append(receipt)
    return touched


# ---------------------------------------------------------------------------
# 航次关联与处置
# ---------------------------------------------------------------------------

_SETTLED_TEXT = {"已改派", "继续签认"}


def _settled(row: dict[str, Any]) -> bool:
    return row.get("status") in _SETTLED_TEXT


def link_voyage(voyage: dict[str, Any]) -> list[dict[str, Any]]:
    """新航次挂到某条航线后，重新核对命中该航线经停的在办回执。"""
    route = require_entity("route", voyage.get("航线编号", ""))
    if route is None or not _text(route.get("经停港口")):
        return []
    with ALERT_LOCK:
        touched: list[dict[str, Any]] = []
        for receipt in _active_receipts():
            sea_area = _text(receipt.get("预警海域"))
            if not sea_area or sea_area not in _text(route.get("经停港口")):
                continue
            refresh_receipt(receipt)
            for alert in _alerts_matching(_text(receipt.get("预警编号"))):
                sync_alert_fields(alert, receipt)
            touched.append(receipt)
        return touched


def _guidance_no_receipt(alert_ref: str) -> str:
    label = alert_ref if alert_ref else "对应预警"
    if label.isdigit():
        label = f"W{label}"
    alert = _alert_by_ref(alert_ref) if alert_ref else None
    if alert is not None and alert.get("status") in ("草稿", "待签收"):
        return (
            f"尚未取得可用补正凭据：预警 {label} 还处于「{alert.get('status', '')}」，"
            f"请先由船长签收预警 {label}，系统会生成补正回执；"
            f"随后在回执上执行「重新核对经停」把本航次核对为受影响，再提交改派。"
        )
    if alert is not None:
        return (
            f"尚未取得可用补正凭据：请在气象预警 {label} 上执行「重新核对经停」，"
            f"由系统生成补正回执（如 {label}-B1）并把本航次核对为受影响后，"
            f"再回来提交改派；补报经停完成后也可以直接重新发起改派。"
        )
    return (
        f"尚未取得可用补正凭据：请先在气象预警 {label} 上确认签收并执行「重新核对经停」，"
        f"生成补正回执并把本航次核对为受影响后，再回来提交改派。"
    )


def _latest_open_receipt() -> dict[str, Any] | None:
    rows = _open_receipts()
    if not rows:
        return None
    return max(rows, key=lambda row: int(row.get("id", 0)))


def _latest_signed_alert_ref() -> str:
    candidates = [
        row for row in store.rows("weather_alert")
        if row.get("status") in ("已签收", "已解除")
    ]
    if not candidates:
        return ""
    latest = max(candidates, key=lambda row: int(row.get("id", 0)))
    return alert_label(latest)


def _resolve_alert_ref(alert_ref: str, receipt_code: str = "") -> str:
    """把动作参数里的各种引用归一成可查询的预警引用。"""
    for value in (alert_ref, receipt_code):
        if not value:
            continue
        alert = _alert_by_ref(value)
        if alert is not None:
            return str(alert.get("id", ""))
        for receipt in store.rows(RECEIPT_MODULE):
            if value in _receipt_refs(receipt):
                return _text(receipt.get("预警记录") or receipt.get("预警编号"))
    return alert_ref


def _related_receipts(voyage: dict[str, Any], alert_ref: str = "") -> list[dict[str, Any]]:
    """返回与该航次相关、且已经刷新过状态的备选回执。

    相关 = 航次出现在回执的受影响/待核实清单里，或回执里已有该航次的处置。
    指定预警时，即使航次还没进入清单，也把该预警的回执带回来，方便给出
    「先补报经停」的明确下一步。
    """
    voyage_id = str(voyage.get("id", ""))
    route = require_entity("route", voyage.get("航线编号", ""))
    via_ports = _text(route.get("经停港口")) if route else ""

    if alert_ref:
        alerts = _alerts_matching(alert_ref)
        if alerts:
            candidates = [
                row for alert in alerts for row in _receipts_for_alert(alert)
                if row.get("status") != "已关闭"
            ]
        else:
            candidates = [
                row for row in store.rows(RECEIPT_MODULE)
                if row.get("status") != "已关闭"
                and alert_ref in _receipt_refs(row)
            ]
    else:
        candidates = [
            row for row in _open_receipts()
            if not via_ports
            or not _text(row.get("预警海域"))
            or _text(row.get("预警海域")) in via_ports
        ]

    related: list[dict[str, Any]] = []
    seen: set[int] = set()
    for receipt in candidates:
        receipt_id = int(receipt.get("id", 0))
        if receipt_id in seen:
            continue
        seen.add(receipt_id)
        assessment = refresh_receipt(receipt)
        affected_ids = {str(row.get("id", "")) for row in assessment["affected"]}
        unconfirmed_ids = {str(item["voyage"].get("id", "")) for item in assessment["unconfirmed"]}
        if (
            alert_ref
            or voyage_id in affected_ids
            or voyage_id in unconfirmed_ids
            or _disposition_for(receipt_id, voyage_id)
        ):
            related.append(receipt)
    return related


def check_voyage_affected(
    voyage: dict[str, Any],
    alert_ref: str = "",
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """返回本航次受哪份回执影响；没有可确认影响时回执为 None。"""
    related = _related_receipts(voyage, alert_ref)
    voyage_id = str(voyage.get("id", ""))
    assessments: dict[str, list[dict[str, Any]]] = {}
    for receipt in related:
        assessment = refresh_receipt(receipt)
        assessments[str(receipt.get("id", ""))] = assessment["affected"]
        if voyage_id in {str(row.get("id", "")) for row in assessment["affected"]}:
            return receipt, related, assessments
    return None, related, assessments


def disposition_credential(receipt: dict[str, Any]) -> str:
    """可被后续改派/签认引用的凭据编号。"""
    return _text(receipt.get("回执编号")) or f"回执#{receipt.get('id', '')}"


def claim_disposition(
    receipt: dict[str, Any],
    voyage: dict[str, Any],
    *,
    kind: str = "先办改派",
    note: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """为受影响航次领取正式处置；同一回执同一航次只保留一份。"""
    with ALERT_LOCK:
        voyage_id = str(voyage.get("id", ""))
        if voyage.get("status") == "已完成":
            return None, f"航次 {voyage_id} 已完成，不再开放处置资格"

        current = store.find(RECEIPT_MODULE, int(receipt.get("id", 0))) or receipt
        if current.get("status") == "已关闭":
            return None, f"补正回执 {current.get('回执编号', '')} 已关闭，不能继续领取处置"

        assessment = refresh_receipt(current)
        affected_ids = {str(row.get("id", "")) for row in assessment["affected"]}
        if voyage_id not in affected_ids:
            reasons = "；".join(item["reason"] for item in assessment["unconfirmed"])
            detail = f"（{reasons}）" if reasons else ""
            return None, (
                f"无法确认航次 {voyage_id} 受预警 {current.get('回执编号', '')} 影响{detail}，"
                f"不能先领正式处置；请先补报经停并重新核对"
            )

        existing = _disposition_for(current.get("id", ""), voyage_id)
        if existing is not None:
            if kind == "继续签认" and existing.get("处置类型") == "先办改派":
                if existing.get("status") == "已改派":
                    return existing, (
                        f"航次 {voyage_id} 的改派已完成（处置编号 {existing.get('处置编号', '')}），"
                        f"同一份回执不再登记新的处置；如需继续签认请在航次上直接办理。"
                    )
                # 同一份补正对同一航次只能有一份处置：把「先办改派」原记录
                # 调整为「继续签认」，而不是再插一条。
                existing["处置类型"] = "继续签认"
                existing["status"] = "继续签认"
                existing["pending"] = False
                existing["处置说明"] = note or existing.get("处置说明", "")
                existing["更新时间"] = _today()
                refresh_receipt(current)
                return existing, (
                    f"航次 {voyage_id} 的处置已由「先办改派」调整为「继续签认」"
                    f"（处置编号 {existing.get('处置编号', '')}），"
                    f"同一次补正不新增第二份处置。"
                )
            return existing, (
                f"回执 {current.get('回执编号', '')} 已为航次 {voyage_id} 登记"
                f"「{existing.get('处置类型', '')}」（{existing.get('处置编号', '')}），"
                f"同一次补正不新增第二份处置"
            )

        row: dict[str, Any] = {
            "id": _next_id(DISPOSITION_MODULE),
            "status": "待改派" if kind == "先办改派" else "继续签认",
            "pending": kind == "先办改派",
            "abnormal": False,
            "处置编号": f"{disposition_credential(current)}-V{voyage_id}",
            "回执编号": disposition_credential(current),
            "回执记录": str(current.get("id", "")),
            "预警编号": current.get("预警编号", ""),
            "航次编号": voyage_id,
            "处置类型": kind,
            "处置说明": note,
            "更新时间": _today(),
            "备注说明": "",
        }
        store.rows(DISPOSITION_MODULE).append(row)
        refresh_receipt(current)
        for alert in _alerts_matching(_text(current.get("预警编号"))):
            sync_alert_fields(alert, current)
        return row, (
            f"已为航次 {voyage_id} 领取处置「{kind}」"
            f"（处置编号 {row['处置编号']}，凭据 {disposition_credential(current)}）。"
        )


def submit_reassignment(
    voyage_id: str | int,
    *,
    alert_ref: str = "",
    receipt_code: str = "",
    note: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """在签认前提交原航次改派；没有可用凭据时返回用预警补办的具体步骤。"""
    with ALERT_LOCK:
        voyage = require_entity("voyage", voyage_id)
        if voyage is None:
            return None, f"航次 {voyage_id} 不存在"
        if voyage.get("status") == "已完成":
            return None, f"航次 {voyage_id} 已完成，不再开放处置资格"

        ref = _resolve_alert_ref(alert_ref, receipt_code)
        receipt, related, _assessments = check_voyage_affected(voyage, ref)

        if receipt is None:
            target = None
            if ref:
                alerts = _alerts_matching(ref)
                for alert in alerts:
                    for row in _receipts_for_alert(alert):
                        if row.get("status") in OPEN_RECEIPT_STATUSES:
                            target = row
                            break
                    if target is not None:
                        break
            if target is None:
                # 没有指定预警时，只用确实关联到本航次的回执，
                # 避免把无关预警的进度误当成改派凭据。
                target = next(
                    (row for row in related if row.get("status") in OPEN_RECEIPT_STATUSES),
                    None,
                )
            if target is not None:
                refresh_receipt(target)
                if target.get("status") == "待核对":
                    return None, (
                        f"补正回执 {target.get('回执编号', '')} 仍处于待核对，"
                        f"尚未确认航次 {voyage_id} 受影响。"
                        f"{_guidance_no_receipt(_text(target.get('预警编号')))}"
                    )
                return None, (
                    f"回执 {target.get('回执编号', '')} 未核对到航次 {voyage_id} 受影响，"
                    f"不能提交改派；无法确认受影响的航次不能先领正式处置。"
                )
            fallback = _latest_signed_alert_ref()
            if not fallback:
                latest = _latest_open_receipt()
                fallback = _text(latest.get("预警编号")) if latest else ""
            return None, _guidance_no_receipt(ref or fallback)

        disposition, msg = claim_disposition(receipt, voyage, kind="先办改派", note=note)
        if disposition is None:
            return None, msg
        return disposition, (
            f"{msg} 签认航次前请先完成改派；如经确认无需改派，"
            f"可执行「继续签认」继续办理。"
        )


def continue_sign_off(
    voyage: dict[str, Any],
    *,
    alert_ref: str = "",
    receipt_code: str = "",
    note: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """确认继续签认：在受影响航次上登记处置并把航次推进到已签认。"""
    with ALERT_LOCK:
        voyage_id = str(voyage.get("id", ""))
        if voyage.get("status") == "已完成":
            return None, f"航次 {voyage_id} 已完成，不能再签认"

        ref = _resolve_alert_ref(alert_ref, receipt_code)
        receipt, related, _assessments = check_voyage_affected(voyage, ref)

        if receipt is None:
            target = None
            if ref:
                for alert in _alerts_matching(ref):
                    for row in _receipts_for_alert(alert):
                        if row.get("status") in OPEN_RECEIPT_STATUSES:
                            target = row
                            break
                    if target is not None:
                        break
            if target is None and related:
                target = next(
                    (row for row in related if row.get("status") == "待核对"),
                    None,
                )
            # 继续签认不领取正式处置；空态/待核对的回执只做提示，
            # 不阻断普通签认，避免把船长卡在未核对到影响的空结果上。
            if target is not None:
                return voyage, (
                    f"补正回执 {target.get('回执编号', '')} 尚未核对到航次 {voyage_id} 受影响，"
                    f"本次按普通签认继续办理，不生成预警处置；"
                    f"如后续补报确认受影响，请再回来补领处置。"
                )
            return voyage, "航次未受预警影响，可直接签认"

        disposition, msg = claim_disposition(receipt, voyage, kind="继续签认", note=note)
        if disposition is None:
            return None, msg
        return voyage, f"{msg} 已按「继续签认」继续办理。"


def sign_off_prompt(voyage: dict[str, Any], alert_ref: str = "") -> str | None:
    """签认前的提示：受影响且处置未落定时，要求区分继续签认和先办改派。

    只在「已确认受影响」时拦截。无法确认受影响（航线待确认/经停未补报）
    的航次不生成处置，也不阻断普通签认，避免把调度员卡在空态上。
    """
    receipt, _related, _assessments = check_voyage_affected(voyage, alert_ref)
    if receipt is None:
        return None

    voyage_id = str(voyage.get("id", ""))
    existing = _disposition_for(receipt.get("id", ""), voyage_id)
    if existing is not None and existing.get("status") != "待改派":
        # 已经完成改派或已经确认继续签认，不再重复拦截。
        return None
    pending_hint = ""
    if existing is not None and existing.get("status") == "待改派":
        pending_hint = (
            f"（已领取「先办改派」，处置编号 {existing.get('处置编号', '')}，尚未完成改派）"
        )
    return (
        f"航次 {voyage_id} 受预警 {receipt.get('回执编号', '')} 影响{pending_hint}，"
        f"签认前需要先确认办理方式：如继续按原计划执行，请选择「继续签认」；"
        f"如需改派船舶或调整航次，请先选择「先办改派」。"
    )
