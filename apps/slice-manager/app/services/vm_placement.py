from typing import Any

from app.services.availability_zones import DEFAULT_ZONE_ID, require_availability_zone


def place_vms(slice_item: dict[str, Any]) -> list[dict[str, Any]]:
    placements = []
    nodes = slice_item.get("nodos") or []
    zone = require_availability_zone(slice_item.get("zona") or DEFAULT_ZONE_ID)
    hosts = zone["hosts"]

    for index, node in enumerate(nodes):
        host = hosts[index % len(hosts)]

        placements.append(
            {
                "node_id": node["id"],
                "target": zone["driver"],
                "driver": zone["driver"],
                "zone": zone["id"],
                "host": host,
                "availability_zone": zone["id"],
                "reason": f"round-robin en {zone['label']}",
            }
        )

    return placements
