import re
from typing import Any

from app.services.availability_zones import DEFAULT_ZONE_ID
from app.services.image_catalog import image_details


ALLOWED_DISK_GB = {1, 2, 3}
MGMT_NODE_ID = "mgmt-cloud"

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
        disk_gb = int(value or 1)
    except (TypeError, ValueError):
        disk_gb = 1
    if disk_gb not in ALLOWED_DISK_GB:
        raise ValueError("Disco no soportado. Usa una opcion de 1, 2 o 3 GB.")
    return disk_gb


def validate_disk_for_image(disk_gb: int, image: dict[str, Any]) -> None:
    try:
        min_disk_gb = int(image.get("min_disk_gb") or 1)
    except (TypeError, ValueError):
        min_disk_gb = 1
    if disk_gb < min_disk_gb:
        label = image.get("label") or image.get("name") or image.get("id") or "la imagen"
        raise ValueError(
            f"La imagen {label} requiere al menos {min_disk_gb} GB de disco. "
            f"Seleccionaste {disk_gb} GB."
        )


def numeric_index(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def topology_node_index(node_id: str) -> int | None:
    match = re.search(r"(?:^|-)n(\d+)$", str(node_id or ""))
    if not match:
        return None
    return int(match.group(1))


def management_enabled_node_ids(slice_item: dict[str, Any]) -> set[str]:
    enabled: set[str] = set()

    for link in slice_item.get("enlaces") or []:
        link_type = str(link.get("tipo") or "").lower()
        topology_type = str(link.get("topologia") or link.get("topo") or "").lower()
        if link_type != "gestion" and topology_type != "management":
            continue

        source = link.get("desde") or link.get("from")
        target = link.get("hacia") or link.get("to")
        if source is None or target is None:
            continue

        source_key = str(source)
        target_key = str(target)
        if source_key == MGMT_NODE_ID and target_key != MGMT_NODE_ID:
            enabled.add(target_key)
        elif target_key == MGMT_NODE_ID and source_key != MGMT_NODE_ID:
            enabled.add(source_key)

    return enabled


def build_script_variables(
    slice_item: dict[str, Any], placements: list[dict[str, Any]]
) -> dict[str, Any]:
    placement_by_node = {item["node_id"]: item for item in placements}
    management_nodes = management_enabled_node_ids(slice_item)
    topology_counters: dict[str, int] = {}
    instances = []

    for index, node in enumerate(slice_item.get("nodos") or []):
        cfg = node.get("configuracion") or {}
        placement = placement_by_node.get(node["id"], {})
        flavor = flavor_details(cfg.get("flavor"))
        image = image_details(cfg.get("imagen"))
        disk_gb = disk_gb_from_config(cfg.get("disco"))
        validate_disk_for_image(disk_gb, image)
        topology_key = str(node.get("topologia_id", ""))
        fallback_node_index = topology_counters.get(topology_key, 0)
        topology_counters[topology_key] = fallback_node_index + 1
        explicit_node_index = numeric_index(node.get("topology_node_index"))
        if explicit_node_index is None:
            explicit_node_index = numeric_index(node.get("topologyNodeIndex"))
        parsed_node_index = topology_node_index(node["id"])
        node_index = (
            explicit_node_index
            if explicit_node_index is not None
            else parsed_node_index
            if parsed_node_index is not None
            else fallback_node_index
        )

        instances.append(
            {
                "name": f"{slice_item['id']}-{node.get('nombre') or node['id']}",
                "node_id": node["id"],
                "node_type": node.get("tipo", "srv"),
                "topology_id": node.get("topologia_id"),
                "topology_node_index": node_index,
                "image": image["id"],
                "image_name": image["name"],
                "image_url": image["url"],
                "image_download_method": image.get("download_method", "auto"),
                "image_cloud_init": True,
                "image_cloud_init_mode": "full",
                "flavor": flavor["name"],
                "vcpus": flavor["vcpus"],
                "ram_mb": flavor["ram_mb"],
                "disk_gb": disk_gb,
                "key_pair": cfg.get("llaves") or "default-key",
                "security_ports": cfg.get("seguridad") or ["22", "443"],
                "custom_rules": cfg.get("reglas") or [],
                "management_enabled": node["id"] in management_nodes,
                "worker_ip": placement.get("host"),
                "availability_zone": placement.get("availability_zone", "nova:compute-1"),
                "fixed_ip": f"10.42.0.{10 + index}",
            }
        )

    return {
        "slice_name": slice_item["nombre"],
        "slice_id": slice_item["id"],
        "zone": slice_item.get("zona") or DEFAULT_ZONE_ID,
        "network_cidr": "10.42.0.0/24",
        "subnet_gateway": "10.42.0.1",
        "instances": instances,
        "links": slice_item.get("enlaces") or [],
        "topologies": slice_item.get("topologias") or [],
    }
