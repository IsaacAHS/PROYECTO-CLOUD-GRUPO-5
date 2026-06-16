import os
from typing import Any


DEFAULT_ZONE_ID = os.getenv("NIMBUSCORE_DEFAULT_ZONE", "linux-zone-1")


def _split_hosts(value: str, fallback: list[str]) -> list[str]:
    hosts = [item.strip() for item in value.split(",") if item.strip()]
    return hosts or fallback


def _zone_catalog() -> dict[str, dict[str, Any]]:
    linux_hosts = _split_hosts(
        os.getenv("NIMBUSCORE_COMPUTE_IPS", "10.0.10.1,10.0.10.2,10.0.10.3,10.0.10.4"),
        ["10.0.10.1", "10.0.10.2", "10.0.10.3", "10.0.10.4"],
    )
    openstack_hosts = _split_hosts(
        os.getenv("NIMBUSCORE_OPENSTACK_COMPUTE_HOSTS", "compute-1,compute-2,compute-3"),
        ["compute-1", "compute-2", "compute-3"],
    )

    return {
        "linux-zone-1": {
            "id": "linux-zone-1",
            "name": "linux-zone-1",
            "label": "Cluster Linux",
            "driver": "linux",
            "hosts": linux_hosts,
            "description": "Cluster Linux con QEMU, OVS y Script Runner.",
            "implemented": True,
        },
        "openstack-zone-1": {
            "id": "openstack-zone-1",
            "name": "openstack-zone-1",
            "label": "Cluster OpenStack",
            "driver": "openstack",
            "hosts": openstack_hosts,
            "description": "Cluster OpenStack mediante SDK/API.",
            "implemented": False,
        },
    }


def list_availability_zones() -> list[dict[str, Any]]:
    return list(_zone_catalog().values())


def get_availability_zone(zone_id: str | None) -> dict[str, Any] | None:
    zone_key = zone_id or DEFAULT_ZONE_ID
    return _zone_catalog().get(zone_key)


def require_availability_zone(zone_id: str | None) -> dict[str, Any]:
    zone = get_availability_zone(zone_id)
    if not zone:
        valid = ", ".join(sorted(_zone_catalog()))
        raise ValueError(f"Zona de disponibilidad invalida. Usa una de: {valid}.")
    return zone
