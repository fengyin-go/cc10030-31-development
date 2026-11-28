"""气象预警空态补正、经停补报与航次处置的业务规则测试。

覆盖题目要求的完整链路：
1. 签收时没有命中航线，仍留下可继续办理的补正回执。
2. 航线补报经停后回执重新核对到受影响航次。
3. 签认前先提交改派；没有可用凭据时给出 W7 级别的补办指引。
4. 同一次补正不新增第二份处置；已完成航次不再开放处置资格。
5. 补报与签认并发时，两边都能看到一致的最新状态。
"""
from __future__ import annotations

import threading

import pytest

from app.services import service_for
from app.services.alert_disposition import AlertDispositionService
from app.services.voyage import VoyageService
from app.services.weather_alert import WeatherAlertService
from app.services.weather_receipt import WeatherReceiptService
from app.store import Store, store


@pytest.fixture(autouse=True)
def _isolate_store(monkeypatch):
    """每个用例从种子数据重新开始，避免动作测试互相污染。"""
    fresh = Store()
    monkeypatch.setattr("app.store.store._tables", fresh._tables)
    yield


def _alert(alert_id: int, sea_area: str, status: str = "待签收") -> dict:
    row = {
        "id": alert_id,
        "status": status,
        "pending": status == "待签收",
        "abnormal": False,
        "预警海域": sea_area,
        "预警类型": "台风",
        "预警等级": "橙色",
        "生效时间": "2026-11-28",
        "预计持续": "24小时",
        "影响航线": "",
        "签发单位": "海事气象台",
        "备注说明": "",
    }
    store.rows("weather_alert").append(row)
    return row


def _route(route_id: int, via_ports: str, status: str = "已确认") -> dict:
    row = {
        "id": route_id,
        "status": status,
        "pending": status != "已确认",
        "abnormal": False,
        "起运港": "宁波港",
        "目的港": "厦门港",
        "经停港口": via_ports,
        "标准航程": "2天",
        "航线等级": "A",
        "适航船舶": "",
        "最近更新": "2026-11-28",
        "备注说明": "",
    }
    store.rows("route").append(row)
    return row


def _voyage(voyage_id: int, route_id: int, status: str = "待签认") -> dict:
    row = {
        "id": voyage_id,
        "status": status,
        "pending": status == "待签认",
        "abnormal": False,
        "船舶编号": "3",
        "航线编号": str(route_id),
        "计划离港": "2026-12-01",
        "计划到港": "2026-12-03",
        "实际离港": "",
        "实际到港": "",
        "航次状态": "",
        "备注说明": "",
    }
    store.rows("voyage").append(row)
    return row


def _receipts() -> list[dict]:
    return store.rows("weather_receipt")


def _dispositions() -> list[dict]:
    return store.rows("alert_disposition")


class TestEmptySignOffLeavesActionableReceipt:
    def test_sign_off_without_hit_creates_pending_receipt(self):
        _alert(7, "舟山外海")
        # 已有航线都不经过舟山外海，签收结果为空。
        entry, msg = WeatherAlertService().run_action(7, "签收预警")

        assert entry is not None and entry["status"] == "已签收"
        assert "暂无命中航线" in msg or "暂未命中航线" in msg
        assert len(_receipts()) == 1
        receipt = _receipts()[0]
        assert receipt["status"] == "待核对"
        assert receipt["回执类型"] == "空态补正"
        assert receipt["预警编号"] == "W7"
        assert receipt["回执编号"] == "W7-B1"

    def test_receipt_list_is_queryable_as_new_todo(self):
        _alert(7, "舟山外海")
        WeatherAlertService().run_action(7, "签收预警")

        items, total = service_for("weather_receipt").list_entries(status="待核对")
        assert total == 1
        assert items[0]["回执编号"] == "W7-B1"
        assert items[0]["pending"] is True

    def test_recheck_keeps_receipt_open_when_still_empty(self):
        _alert(7, "舟山外海")
        WeatherAlertService().run_action(7, "签收预警")
        _entry, msg = WeatherAlertService().run_action(7, "重新核对经停")

        assert "待核对" in msg
        assert _receipts()[0]["status"] == "待核对"


class TestRouteCorrectionRelinksReceipt:
    def test_route_adds_sea_area_and_receipt_finds_voyage(self):
        _alert(7, "舟山外海")
        _voyage(20, 10)
        _route(10, "青岛外海")
        WeatherAlertService().run_action(7, "签收预警")
        assert _receipts()[0]["status"] == "待核对"

        entry, msg = service_for("route").run_action(10, "调整经停", {"经停港口": "青岛外海、舟山外海"})

        assert entry is not None and entry["status"] == "已确认"
        assert "W7-B1" in msg
        receipt = _receipts()[0]
        assert receipt["status"] == "待处置"
        assert receipt["受影响航次"] == "20"

    def test_unconfirmed_route_does_not_grant_disposition(self):
        _alert(7, "舟山外海")
        _route(10, "舟山外海", status="待确认")
        _voyage(20, 10)
        WeatherAlertService().run_action(7, "签收预警")
        # 航线尚未确认，即使经停写了预警海域也不能确认受影响。
        WeatherReceiptService().run_action(_receipts()[0]["id"], "重新核对")

        entry, msg = VoyageService().run_action(20, "先办改派", {"alert_ref": "W7"})
        assert entry is None
        assert "无法确认" in msg or "待核对" in msg
        assert _dispositions() == []


class TestReassignBeforeSignOff:
    def test_reassign_without_credential_points_to_w7_recovery(self):
        _alert(7, "舟山外海")
        _voyage(20, 10)
        _route(10, "青岛外海")
        WeatherAlertService().run_action(7, "签收预警")

        entry, msg = VoyageService().run_action(20, "先办改派", {"alert_ref": "W7"})

        assert entry is None
        assert "尚未取得可用补正凭据" in msg
        assert "重新核对经停" in msg
        assert "W7" in msg

    def test_sign_off_is_blocked_with_two_way_prompt(self):
        _alert(7, "舟山外海")
        _voyage(20, 10)
        _route(10, "舟山外海")
        WeatherAlertService().run_action(7, "签收预警")

        entry, msg = VoyageService().run_action(20, "签认航次", {"alert_ref": "W7"})

        assert entry is None
        assert "继续签认" in msg and "先办改派" in msg
        assert store.find("voyage", 20)["status"] == "待签认"

    def test_reassign_then_continue_sign_off(self):
        _alert(7, "舟山外海")
        _voyage(20, 10)
        _route(10, "舟山外海")
        WeatherAlertService().run_action(7, "签收预警")
        service_for("route").run_action(10, "重新核对经停")

        disposition, msg = VoyageService().run_action(20, "先办改派", {"alert_ref": "W7"})
        assert disposition is not None
        assert "W7-B1" in msg
        assert len(_dispositions()) == 1

        # 签认前改派未完成，仍要提示区分。
        entry, prompt = VoyageService().run_action(20, "签认航次", {"alert_ref": "W7"})
        assert entry is None and "先办改派" in prompt

        # 完成改派后可以签认。
        AlertDispositionService().run_action(_dispositions()[0]["id"], "完成改派")
        entry, _msg = VoyageService().run_action(20, "签认航次", {"alert_ref": "W7"})
        assert entry is not None and entry["status"] == "已签认"

    def test_continue_sign_off_resolves_without_second_disposition(self):
        _alert(7, "舟山外海")
        _voyage(20, 10)
        _route(10, "舟山外海")
        WeatherAlertService().run_action(7, "签收预警")
        service_for("route").run_action(10, "重新核对经停")

        VoyageService().run_action(20, "先办改派", {"alert_ref": "W7"})
        entry, msg = VoyageService().run_action(20, "继续签认", {"alert_ref": "W7"})

        assert entry is not None and entry["status"] == "已签认"
        assert "继续签认" in msg
        assert len(_dispositions()) == 1
        assert _dispositions()[0]["处置类型"] == "继续签认"


class TestNoDuplicateDisposition:
    def test_same_receipt_never_adds_second_disposition(self):
        _alert(7, "舟山外海")
        _voyage(20, 10)
        _route(10, "舟山外海")
        WeatherAlertService().run_action(7, "签收预警")
        service_for("route").run_action(10, "重新核对经停")

        first, _ = VoyageService().run_action(20, "先办改派", {"alert_ref": "W7"})
        second, msg = VoyageService().run_action(20, "先办改派", {"alert_ref": "W7"})

        assert first is not None and second is not None
        assert len(_dispositions()) == 1
        assert "不新增第二份处置" in msg
        assert _dispositions()[0]["处置编号"] == "W7-B1-V20"

    def test_completed_voyage_has_no_disposition_rights(self):
        _alert(7, "舟山外海")
        _voyage(20, 10, status="已完成")
        _route(10, "舟山外海")
        WeatherAlertService().run_action(7, "签收预警")
        service_for("route").run_action(10, "重新核对经停")

        entry, msg = VoyageService().run_action(20, "先办改派", {"alert_ref": "W7"})
        assert entry is None
        assert "已完成" in msg and "不再开放处置资格" in msg
        assert _dispositions() == []
        # 已完成航次不计入回执的受影响清单。
        assert _receipts()[0]["受影响航次"] == ""


class TestConcurrentCorrectionAndSignOff:
    def test_route_correction_and_sign_off_race_stays_consistent(self):
        _alert(7, "舟山外海")
        _voyage(20, 10)
        _route(10, "青岛外海")
        WeatherAlertService().run_action(7, "签收预警")

        barrier = threading.Barrier(2, timeout=5)
        results: dict[str, tuple] = {}

        def correct_route():
            barrier.wait()
            results["route"] = service_for("route").run_action(
                10, "调整经停", {"经停港口": "青岛外海、舟山外海"})

        def sign_voyage():
            barrier.wait()
            results["sign"] = VoyageService().run_action(
                20, "签认航次", {"alert_ref": "W7"})

        threads = [threading.Thread(target=correct_route), threading.Thread(target=sign_voyage)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert results["route"][0] is not None
        assert _receipts()[0]["受影响航次"] == "20"
        # 要么签认被拦下等待选择办理方式，要么在补报前完成普通签认；
        # 无论先后，都不能出现「补报后仍把受影响航次当作未受影响」。
        sign_entry, sign_msg = results["sign"]
        if sign_entry is not None:
            assert store.find("voyage", 20)["status"] == "已签认"
        else:
            assert "继续签认" in sign_msg

    def test_simultaneous_recheck_calls_share_one_receipt(self):
        _alert(7, "舟山外海")
        _voyage(20, 10)
        _route(10, "舟山外海")
        WeatherAlertService().run_action(7, "签收预警")

        barrier = threading.Barrier(4, timeout=5)

        def recheck():
            barrier.wait()
            WeatherAlertService().run_action(7, "重新核对经停")

        threads = [threading.Thread(target=recheck) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(_receipts()) == 1


class TestMissedVoyageAfterSignOff:
    def test_voyage_signed_before_supplement_is_still_caught(self):
        """另一条路径先完成签认；补报后仍要能把错过的船只补进处置。"""
        _alert(7, "舟山外海")
        _voyage(20, 10)
        _route(10, "青岛外海")
        WeatherAlertService().run_action(7, "签收预警")

        # 补报还没到，船长先按普通流程签认完成。
        entry, _msg = VoyageService().run_action(20, "签认航次")
        assert entry is not None and entry["status"] == "已签认"

        # 稍后航线补报该海域，回执应把这条已签认的航次重新捞出来。
        service_for("route").run_action(10, "补报经停", {"经停港口": "青岛外海、舟山外海"})
        receipt = _receipts()[0]
        assert receipt["受影响航次"] == "20"
        assert receipt["status"] == "待处置"

        # 已签认但未完成的航次仍可领取补正处置。
        disposition, msg = VoyageService().run_action(20, "先办改派", {"alert_ref": "W7"})
        assert disposition is not None
        assert "W7-B1-V20" in msg

    def test_completed_voyage_never_appears_in_affected_list(self):
        _alert(7, "舟山外海")
        _voyage(20, 10, status="已完成")
        _route(10, "舟山外海")
        WeatherAlertService().run_action(7, "签收预警")
        service_for("route").run_action(10, "重新核对经停")

        receipt = _receipts()[0]
        assert receipt["受影响航次"] == ""
        assert "20" in receipt["已完成航次"]
        assert receipt["status"] == "待核对"

    def test_receipt_recheck_action_refreshes_affected_voyages(self):
        _alert(7, "舟山外海")
        _voyage(20, 10)
        _route(10, "青岛外海")
        WeatherAlertService().run_action(7, "签收预警")
        # 直接在航线数据上补报，模拟外部数据同步。
        store.find("route", 10)["经停港口"] = "青岛外海、舟山外海"

        receipt = _receipts()[0]
        entry, msg = WeatherReceiptService().run_action(receipt["id"], "重新核对")

        assert entry is not None
        assert entry["受影响航次"] == "20"
        assert entry["status"] == "待处置"
        assert "20" in msg
