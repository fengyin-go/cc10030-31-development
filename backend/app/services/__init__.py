"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.vessel import VesselService
from app.services.route import RouteService
from app.services.voyage import VoyageService
from app.services.cargo import CargoService
from app.services.crew_shift import CrewShiftService
from app.services.fuel_log import FuelLogService
from app.services.port_call import PortCallService
from app.services.container import ContainerService
from app.services.weather_alert import WeatherAlertService
from app.services.weather_receipt import WeatherReceiptService
from app.services.alert_disposition import AlertDispositionService
from app.services.accident_log import AccidentLogService
from app.services.inspection import InspectionService
from app.services.clearance import ClearanceService
from app.services.charter import CharterService
from app.services.berth import BerthService
from app.services.manifest import ManifestService
from app.services.voyage_log import VoyageLogService
from app.services.maintenance import MaintenanceService
from app.services.crew_cert import CrewCertService
from app.services.dispatch_note import DispatchNoteService
from app.services.tariff import TariffService

SERVICES: dict[str, BaseModuleService] = {
    "vessel": VesselService(),
    "route": RouteService(),
    "voyage": VoyageService(),
    "cargo": CargoService(),
    "crew_shift": CrewShiftService(),
    "fuel_log": FuelLogService(),
    "port_call": PortCallService(),
    "container": ContainerService(),
    "weather_alert": WeatherAlertService(),
    "weather_receipt": WeatherReceiptService(),
    "alert_disposition": AlertDispositionService(),
    "accident_log": AccidentLogService(),
    "inspection": InspectionService(),
    "clearance": ClearanceService(),
    "charter": CharterService(),
    "berth": BerthService(),
    "manifest": ManifestService(),
    "voyage_log": VoyageLogService(),
    "maintenance": MaintenanceService(),
    "crew_cert": CrewCertService(),
    "dispatch_note": DispatchNoteService(),
    "tariff": TariffService(),
}


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
