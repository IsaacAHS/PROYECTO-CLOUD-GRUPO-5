from app.drivers.base import ClusterDriver
from app.drivers.linux import LinuxClusterDriver
from app.drivers.openstack import OpenStackClusterDriver
from app.services.availability_zones import DEFAULT_ZONE_ID, require_availability_zone


def select_cluster_driver(slice_item: dict) -> ClusterDriver:
    zone = require_availability_zone(slice_item.get("zona") or DEFAULT_ZONE_ID)
    driver = str(zone.get("driver") or "").lower()
    if driver == "linux":
        return LinuxClusterDriver(zone)
    if driver == "openstack":
        return OpenStackClusterDriver(zone)
    raise ValueError(f"Driver no soportado para la zona {zone['id']}: {driver}")
