from typing import Any

from app.services.image_catalog import image_details


FLAVORS = {
    "m1.tiny": {"vcpus": 1, "ram_mb": 512},
    "m1.small": {"vcpus": 1, "ram_mb": 2048},
    "m1.medium": {"vcpus": 2, "ram_mb": 4096},
    "m1.large": {"vcpus": 4, "ram_mb": 8192},
    "m1.xlarge": {"vcpus": 8, "ram_mb": 16384},
}

def flavor_details(flavor_name: str | None) -> dict[str, int | str]:
    name = flavor_name or "m1.small"
    spec = FLAVORS.get(name, FLAVORS["m1.small"])
    return {"name": name, **spec}


def disk_gb_from_config(value: Any) -> int:
    try:
        disk_gb = int(value or 20)
    except (TypeError, ValueError):
        disk_gb = 20
    return max(disk_gb, 1)


def topology_node_index(node_id: str) -> int:
    try:
        return int(node_id.rsplit("-n", 1)[1])
    except (IndexError, ValueError):
        return 0


def build_script_variables(
    slice_item: dict[str, Any], placements: list[dict[str, Any]]
) -> dict[str, Any]:
    placement_by_node = {item["node_id"]: item for item in placements}
    instances = []

    for index, node in enumerate(slice_item.get("nodos") or []):
        cfg = node.get("configuracion") or {}
        placement = placement_by_node.get(node["id"], {})
        flavor = flavor_details(cfg.get("flavor"))
        image = image_details(cfg.get("imagen"))

        instances.append(
            {
                "name": f"{slice_item['id']}-{node.get('nombre') or node['id']}",
                "node_id": node["id"],
                "node_type": node.get("tipo", "srv"),
                "topology_id": node.get("topologia_id"),
                "topology_node_index": topology_node_index(node["id"]),
                "image": image["id"],
                "image_name": image["name"],
                "image_url": image["url"],
                "image_download_method": image.get("download_method", "auto"),
                "image_cloud_init": bool(image.get("cloud_init", True)),
                "flavor": flavor["name"],
                "vcpus": flavor["vcpus"],
                "ram_mb": flavor["ram_mb"],
                "disk_gb": disk_gb_from_config(cfg.get("disco")),
                "key_pair": cfg.get("llaves") or "default-key",
                "security_ports": cfg.get("seguridad") or ["22", "443"],
                "custom_rules": cfg.get("reglas") or [],
                "availability_zone": placement.get("availability_zone", "nova:compute-1"),
                "fixed_ip": f"10.42.0.{10 + index}",
            }
        )

    return {
        "slice_name": slice_item["nombre"],
        "slice_id": slice_item["id"],
        "zone": slice_item.get("zona") or "openstack-zone-1",
        "network_cidr": "10.42.0.0/24",
        "subnet_gateway": "10.42.0.1",
        "instances": instances,
        "links": slice_item.get("enlaces") or [],
        "topologies": slice_item.get("topologias") or [],
    }
