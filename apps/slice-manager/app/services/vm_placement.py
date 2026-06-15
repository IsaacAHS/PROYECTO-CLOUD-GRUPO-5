from typing import Any


OPENSTACK_HOSTS = ["compute-1", "compute-2", "compute-3"]
LINUX_HOSTS = ["10.0.10.1", "10.0.10.2", "10.0.10.3", "10.0.10.4"]


def place_vms(slice_item: dict[str, Any]) -> list[dict[str, Any]]:
    placements = []
    nodes = slice_item.get("nodos") or []

    for index, node in enumerate(nodes):
        cfg = node.get("configuracion") or {}
        flavor = cfg.get("flavor") or "m1.small"
        target = "linux" if flavor.startswith("linux.") else "openstack"
        hosts = LINUX_HOSTS if target == "linux" else OPENSTACK_HOSTS
        host = hosts[index % len(hosts)]

        placements.append(
            {
                "node_id": node["id"],
                "target": target,
                "host": host,
                "availability_zone": f"nova:{host}" if target == "openstack" else host,
                "reason": "round-robin demo hardcodeado",
            }
        )

    return placements
