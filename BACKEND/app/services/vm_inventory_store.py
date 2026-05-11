import json
import os
from pathlib import Path
from typing import Any


VM_INVENTORY_PATH = Path(
    os.getenv("NIMBUSCORE_VM_INVENTORY_PATH", "/script-runs/vm_inventory.json")
)


def load_inventory_store() -> dict[str, Any]:
    if not VM_INVENTORY_PATH.exists():
        return {"updated_at": None, "slices": {}}

    try:
        raw = json.loads(VM_INVENTORY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"updated_at": None, "slices": {}}

    if not isinstance(raw, dict):
        return {"updated_at": None, "slices": {}}

    slices = raw.get("slices")
    if not isinstance(slices, dict):
        raw["slices"] = {}
    return raw


def inventory_for_slice(slice_id: str) -> dict[str, Any] | None:
    store = load_inventory_store()
    inventory = store.get("slices", {}).get(slice_id)
    return inventory if isinstance(inventory, dict) else None
