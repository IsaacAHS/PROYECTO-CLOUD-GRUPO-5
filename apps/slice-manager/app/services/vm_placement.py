import hashlib
from typing import Any

from app.services.availability_zones import DEFAULT_ZONE_ID, require_availability_zone
from app.services.slice_store import load_deployments
from app.services.vm_inventory_store import load_inventory_store


IGNORED_VM_STATUSES = {"DESTROYED", "FAILED"}
ACTIVE_JOB_STATUSES = {"QUEUED", "RUNNING", "SUCCESS"}


def stable_rank(seed: str, value: str) -> int:
    digest = hashlib.sha1(f"{seed}:{value}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def current_vm_counts(hosts: list[str], slice_id: str) -> dict[str, int]:
    counts = {host: 0 for host in hosts}
    store = load_inventory_store()

    for inventory in store.get("slices", {}).values():
        if str(inventory.get("slice_id") or "") == slice_id:
            continue

        for vm in inventory.get("vms") or []:
            status = str(vm.get("status") or "").upper()
            if status in IGNORED_VM_STATUSES:
                continue

            host = str(vm.get("worker_ip") or vm.get("host") or "").strip()
            if host in counts:
                counts[host] += 1

    for deployment in load_deployments().values():
        if str(deployment.get("slice_id") or "") == slice_id:
            continue

        status = str(deployment.get("status") or "").upper()
        if status not in ACTIVE_JOB_STATUSES:
            continue

        action = deployment.get("script_runner", {}).get("action")
        if action != "create_topology":
            continue

        for placement in deployment.get("placements") or []:
            host = str(placement.get("host") or "").strip()
            if host in counts:
                counts[host] += 1

    return counts


def place_vms(slice_item: dict[str, Any]) -> list[dict[str, Any]]:
    placements = []
    nodes = slice_item.get("nodos") or []
    zone = require_availability_zone(slice_item.get("zona") or DEFAULT_ZONE_ID)
    hosts = [str(host) for host in zone["hosts"]]
    slice_id = str(slice_item.get("id") or slice_item.get("nombre") or "slice")
    counts = current_vm_counts(hosts, slice_id)

    for index, node in enumerate(nodes):
        host = min(
            hosts,
            key=lambda candidate: (
                counts.get(candidate, 0),
                stable_rank(f"{slice_id}:{index}", candidate),
            ),
        )
        counts[host] = counts.get(host, 0) + 1

        placements.append(
            {
                "node_id": node["id"],
                "target": zone["driver"],
                "driver": zone["driver"],
                "zone": zone["id"],
                "host": host,
                "availability_zone": zone["id"],
                "reason": f"least-used en {zone['label']}",
            }
        )

    return placements
