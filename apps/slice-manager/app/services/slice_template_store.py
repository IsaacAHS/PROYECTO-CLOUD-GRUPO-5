import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SLICE_TEMPLATE_STORE_PATH = Path(
    os.getenv("NIMBUSCORE_SLICE_TEMPLATE_STORE_PATH", "/data/slice_templates.json")
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_templates() -> dict[str, dict[str, Any]]:
    SLICE_TEMPLATE_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not SLICE_TEMPLATE_STORE_PATH.exists():
        write_templates({})
        return {}

    try:
        raw = json.loads(SLICE_TEMPLATE_STORE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        write_templates({})
        return {}

    templates = raw.get("templates", raw) if isinstance(raw, dict) else {}
    if isinstance(templates, list):
        return {
            str(item["id"]): item
            for item in templates
            if isinstance(item, dict) and item.get("id")
        }
    if isinstance(templates, dict):
        return {
            str(item_id): item
            for item_id, item in templates.items()
            if isinstance(item, dict)
        }
    return {}


def write_templates(templates: dict[str, dict[str, Any]]) -> None:
    SLICE_TEMPLATE_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SLICE_TEMPLATE_STORE_PATH.write_text(
        json.dumps(
            {
                "updated_at": now_iso(),
                "templates": templates,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
