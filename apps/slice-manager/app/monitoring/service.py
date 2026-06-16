import os
import time
from threading import Lock
from typing import Any

from app.monitoring.models import (
    ClusterMetricsSnapshot,
    HostAllocations,
    HostCapacity,
    HostMetrics,
    HostRealUsage,
    OverbookingPolicy,
)
from app.services.availability_zones import list_availability_zones, require_availability_zone


_CACHE_LOCK = Lock()
_SNAPSHOT_CACHE: dict[str, tuple[float, ClusterMetricsSnapshot]] = {}


def float_from_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def int_from_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def default_overbooking_policy() -> OverbookingPolicy:
    return OverbookingPolicy(
        cpu_allocation_ratio=float_from_env("NIMBUSCORE_CPU_ALLOCATION_RATIO", 1.0),
        ram_allocation_ratio=float_from_env("NIMBUSCORE_RAM_ALLOCATION_RATIO", 1.0),
        reserved_ram_mb=int_from_env("NIMBUSCORE_RESERVED_RAM_MB", 0),
    )


def empty_host_metrics(zone: dict[str, Any], host: str) -> HostMetrics:
    return HostMetrics(
        host_id=host,
        hostname=host,
        zone=str(zone["id"]),
        driver=str(zone["driver"]),
        status="unknown",
        capacity=HostCapacity(),
        real_usage=HostRealUsage(),
        allocations=HostAllocations(),
        policy=default_overbooking_policy(),
        source="availability-zone-catalog",
        errors=["Monitoreo real pendiente de implementar para este driver."],
    )


def unknown_zone_snapshot(zone: dict[str, Any]) -> ClusterMetricsSnapshot:
    return ClusterMetricsSnapshot(
        hosts=[
            empty_host_metrics(zone, str(host))
            for host in zone.get("hosts", [])
        ],
        source="availability-zone-catalog",
    )


def collect_zone_snapshot(zone: dict[str, Any]) -> ClusterMetricsSnapshot:
    driver = str(zone.get("driver") or "").lower()
    if driver == "linux":
        from app.monitoring.linux import LinuxMonitor

        return LinuxMonitor().snapshot(zone)
    return unknown_zone_snapshot(zone)


def cache_key(zone_id: str | None) -> str:
    return zone_id or "__all__"


def cached_snapshot(zone_id: str | None) -> ClusterMetricsSnapshot | None:
    ttl = int_from_env("NIMBUSCORE_MONITOR_CACHE_TTL_SECONDS", 15)
    if ttl <= 0:
        return None
    with _CACHE_LOCK:
        cached = _SNAPSHOT_CACHE.get(cache_key(zone_id))
    if not cached:
        return None
    cached_at, snapshot = cached
    if time.time() - cached_at > ttl:
        return None
    return snapshot


def store_snapshot(zone_id: str | None, snapshot: ClusterMetricsSnapshot) -> None:
    ttl = int_from_env("NIMBUSCORE_MONITOR_CACHE_TTL_SECONDS", 15)
    if ttl <= 0:
        return
    with _CACHE_LOCK:
        _SNAPSHOT_CACHE[cache_key(zone_id)] = (time.time(), snapshot)


def monitoring_snapshot(zone_id: str | None = None) -> ClusterMetricsSnapshot:
    cached = cached_snapshot(zone_id)
    if cached:
        return cached

    zones = [require_availability_zone(zone_id)] if zone_id else list_availability_zones()
    snapshots = [collect_zone_snapshot(zone) for zone in zones]
    hosts = [host for snapshot in snapshots for host in snapshot.hosts]
    source = snapshots[0].source if len(snapshots) == 1 else "mixed"
    snapshot = ClusterMetricsSnapshot(
        hosts=hosts,
        source=source,
    )
    store_snapshot(zone_id, snapshot)
    return snapshot
