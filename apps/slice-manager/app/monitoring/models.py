from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


HostStatus = Literal["online", "offline", "unknown", "degraded"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def optional_round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


@dataclass(frozen=True)
class HostCapacity:
    vcpus_total: int | None = None
    ram_total_mb: int | None = None
    disk_total_gb: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "vcpus_total": self.vcpus_total,
            "ram_total_mb": self.ram_total_mb,
            "disk_total_gb": self.disk_total_gb,
        }


@dataclass(frozen=True)
class HostRealUsage:
    cpu_used_percent: float | None = None
    ram_used_mb: int | None = None
    ram_available_mb: int | None = None
    ram_used_percent: float | None = None
    disk_used_percent: float | None = None
    qemu_processes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_used_percent": optional_round(self.cpu_used_percent),
            "ram_used_mb": self.ram_used_mb,
            "ram_available_mb": self.ram_available_mb,
            "ram_used_percent": optional_round(self.ram_used_percent),
            "disk_used_percent": optional_round(self.disk_used_percent),
            "qemu_processes": self.qemu_processes,
        }


@dataclass(frozen=True)
class HostAllocations:
    allocated_vcpus: int = 0
    allocated_ram_mb: int = 0
    allocated_disk_gb: int = 0
    running_vms: int = 0
    planned_vms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "allocated_vcpus": self.allocated_vcpus,
            "allocated_ram_mb": self.allocated_ram_mb,
            "allocated_disk_gb": self.allocated_disk_gb,
            "running_vms": self.running_vms,
            "planned_vms": self.planned_vms,
        }


@dataclass(frozen=True)
class OverbookingPolicy:
    cpu_allocation_ratio: float = 1.0
    ram_allocation_ratio: float = 1.0
    reserved_ram_mb: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_allocation_ratio": self.cpu_allocation_ratio,
            "ram_allocation_ratio": self.ram_allocation_ratio,
            "reserved_ram_mb": self.reserved_ram_mb,
        }


@dataclass(frozen=True)
class HostMetrics:
    host_id: str
    hostname: str
    zone: str
    driver: str
    status: HostStatus
    capacity: HostCapacity = field(default_factory=HostCapacity)
    real_usage: HostRealUsage = field(default_factory=HostRealUsage)
    allocations: HostAllocations = field(default_factory=HostAllocations)
    policy: OverbookingPolicy = field(default_factory=OverbookingPolicy)
    source: str = "unknown"
    updated_at: str = field(default_factory=now_iso)
    errors: list[str] = field(default_factory=list)

    def effective_vcpu_capacity(self) -> int | None:
        if self.capacity.vcpus_total is None:
            return None
        return int(self.capacity.vcpus_total * self.policy.cpu_allocation_ratio)

    def effective_ram_capacity_mb(self) -> int | None:
        if self.capacity.ram_total_mb is None:
            return None
        effective = int(self.capacity.ram_total_mb * self.policy.ram_allocation_ratio)
        return max(effective - self.policy.reserved_ram_mb, 0)

    def available_vcpus_by_allocation(self) -> int | None:
        effective = self.effective_vcpu_capacity()
        if effective is None:
            return None
        return max(effective - self.allocations.allocated_vcpus, 0)

    def available_ram_mb_by_allocation(self) -> int | None:
        effective = self.effective_ram_capacity_mb()
        if effective is None:
            return None
        return max(effective - self.allocations.allocated_ram_mb, 0)

    def to_dict(self) -> dict[str, Any]:
        effective_vcpus = self.effective_vcpu_capacity()
        effective_ram_mb = self.effective_ram_capacity_mb()
        return {
            "host_id": self.host_id,
            "hostname": self.hostname,
            "zone": self.zone,
            "driver": self.driver,
            "status": self.status,
            "capacity": self.capacity.to_dict(),
            "real_usage": self.real_usage.to_dict(),
            "allocations": self.allocations.to_dict(),
            "overbooking_policy": self.policy.to_dict(),
            "effective_capacity": {
                "vcpus": effective_vcpus,
                "ram_mb": effective_ram_mb,
            },
            "available_by_allocation": {
                "vcpus": self.available_vcpus_by_allocation(),
                "ram_mb": self.available_ram_mb_by_allocation(),
            },
            "source": self.source,
            "updated_at": self.updated_at,
            "errors": self.errors,
        }


@dataclass(frozen=True)
class ClusterMetricsSnapshot:
    hosts: list[HostMetrics]
    source: str
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "updated_at": self.updated_at,
            "source": self.source,
            "hosts": [host.to_dict() for host in self.hosts],
        }
