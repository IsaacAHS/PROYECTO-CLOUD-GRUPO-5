from dataclasses import dataclass
from typing import Any


class DriverNotImplementedError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeployJobRequest:
    job_id: str
    slice_item: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class DestroyJobRequest:
    job_id: str
    slice_item: dict[str, Any]
    inventory: dict[str, Any]
    created_at: str


class ClusterDriver:
    name = "base"
    implemented = False

    def __init__(self, zone: dict[str, Any]) -> None:
        self.zone = zone

    @property
    def zone_id(self) -> str:
        return str(self.zone["id"])

    @property
    def label(self) -> str:
        return str(self.zone.get("label") or self.zone_id)

    def not_implemented_message(self) -> str:
        return f"El driver para {self.label} todavia no esta implementado."

    def build_deploy_job(self, request: DeployJobRequest) -> dict[str, Any]:
        raise DriverNotImplementedError(self.not_implemented_message())

    def build_destroy_job(self, request: DestroyJobRequest) -> dict[str, Any]:
        raise DriverNotImplementedError(self.not_implemented_message())
