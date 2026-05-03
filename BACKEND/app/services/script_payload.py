from typing import Any


def build_script_variables(
    slice_item: dict[str, Any], placements: list[dict[str, Any]]
) -> dict[str, Any]:
    placement_by_node = {item["node_id"]: item for item in placements}
    instances = []

    for index, node in enumerate(slice_item.get("nodos") or []):
        cfg = node.get("configuracion") or {}
        placement = placement_by_node.get(node["id"], {})

        instances.append(
            {
                "name": f"{slice_item['id']}-{node.get('nombre') or node['id']}",
                "node_id": node["id"],
                "node_type": node.get("tipo", "srv"),
                "image": cfg.get("imagen") or "ubuntu-22",
                "flavor": cfg.get("flavor") or "m1.small",
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
