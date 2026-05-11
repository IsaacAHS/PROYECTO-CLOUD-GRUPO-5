import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SLICE_STORE_PATH = Path(os.getenv("NIMBUSCORE_SLICE_STORE_PATH", "/data/slices.json"))
DEPLOYMENT_STORE_PATH = Path(os.getenv("NIMBUSCORE_DEPLOYMENT_STORE_PATH", "/data/deployments.json"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_mapping(path: Path, key: str) -> dict[str, dict[str, Any]]:
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        write_mapping(path, key, {})
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        write_mapping(path, key, {})
        return {}

    items = raw.get(key, raw) if isinstance(raw, dict) else {}
    if isinstance(items, list):
        return {str(item["id"]): item for item in items if isinstance(item, dict) and item.get("id")}
    if isinstance(items, dict):
        return {str(item_id): item for item_id, item in items.items() if isinstance(item, dict)}
    return {}


def write_mapping(path: Path, key: str, items: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "updated_at": now_iso(),
                key: items,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def load_slices() -> dict[str, dict[str, Any]]:
    return read_mapping(SLICE_STORE_PATH, "slices")


def save_slices(slices: dict[str, dict[str, Any]]) -> None:
    write_mapping(SLICE_STORE_PATH, "slices", slices)


def load_deployments() -> dict[str, dict[str, Any]]:
    return read_mapping(DEPLOYMENT_STORE_PATH, "deployments")


def save_deployments(deployments: dict[str, dict[str, Any]]) -> None:
    write_mapping(DEPLOYMENT_STORE_PATH, "deployments", deployments)
