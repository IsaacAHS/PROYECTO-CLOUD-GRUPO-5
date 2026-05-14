import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


IMAGE_CATALOG_PATH = Path(os.getenv("NIMBUSCORE_IMAGE_CATALOG_PATH", "/data/images.json"))
IMAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ALLOWED_DOWNLOAD_METHODS = {"auto", "wget-no-check-certificate"}

DEFAULT_IMAGES = [
    {
        "id": "cirros",
        "name": "cirros-0.6.2",
        "label": "Cirros 0.6.2",
        "url": "https://download.cirros-cloud.net/0.6.2/cirros-0.6.2-x86_64-disk.img",
        "download_method": "auto",
        "cloud_init": False,
        "active": True,
    },
    {
        "id": "cirros-drive",
        "name": "cirros-0.6.2-x86_64-disk.img",
        "label": "Cirros 0.6.2 (Google Drive)",
        "url": "https://drive.usercontent.google.com/download?id=1TzJ7mOs-b-Ggwr9lXvcNbYiMVqfTlKH9&export=download&confirm=t",
        "download_method": "wget-no-check-certificate",
        "cloud_init": False,
        "active": True,
    },
    {
        "id": "ubuntu-22",
        "name": "ubuntu-22.04-jammy",
        "label": "Ubuntu 22.04 LTS",
        "url": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img",
        "download_method": "auto",
        "cloud_init": True,
        "active": True,
    },
    {
        "id": "ubuntu-20",
        "name": "ubuntu-20.04-focal",
        "label": "Ubuntu 20.04 LTS",
        "url": "https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-amd64.img",
        "download_method": "auto",
        "cloud_init": True,
        "active": True,
    },
    {
        "id": "ubuntu-20-drive",
        "name": "focal-server-cloudimg-amd64.img",
        "label": "Ubuntu 20.04 Focal (Google Drive)",
        "url": "https://drive.usercontent.google.com/download?id=169719Mq3URSPKf2y6x-uAJ0vluH31i5n&export=download&confirm=t",
        "download_method": "wget-no-check-certificate",
        "cloud_init": False,
        "active": True,
    },
    {
        "id": "debian-12",
        "name": "debian-12-bookworm",
        "label": "Debian 12",
        "url": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2",
        "download_method": "auto",
        "cloud_init": True,
        "active": True,
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def bool_from_item(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def infer_cloud_init(item: dict[str, Any], image_id: str, url: str) -> bool:
    if item.get("cloud_init") is not None:
        return bool_from_item(item.get("cloud_init"))
    if image_id == "cirros" or image_id.endswith("-drive"):
        return False
    if item.get("source") == "google-drive-rclone":
        return False
    if "drive.usercontent.google.com" in url or "drive.google.com" in url:
        return False
    return True


def normalize_image(item: dict[str, Any]) -> dict[str, Any]:
    image_id = str(item.get("id") or "").strip()
    name = str(item.get("name") or image_id).strip()
    label = str(item.get("label") or name).strip()
    url = str(item.get("url") or "").strip()
    download_method = str(item.get("download_method") or "auto").strip()

    if not IMAGE_ID_RE.match(image_id):
        raise ValueError(f"ID de imagen invalido: {image_id!r}")
    if not name:
        raise ValueError(f"Nombre de imagen vacio para {image_id}")
    if not url:
        raise ValueError(f"URL de imagen vacia para {image_id}")
    if download_method not in ALLOWED_DOWNLOAD_METHODS:
        raise ValueError(f"Metodo de descarga no soportado: {download_method}")

    normalized = {
        "id": image_id,
        "name": name,
        "label": label,
        "url": url,
        "download_method": download_method,
        "cloud_init": infer_cloud_init(item, image_id, url),
        "active": bool(item.get("active", True)),
    }
    for optional_key in (
        "source",
        "drive_file_id",
        "drive_public_link",
        "drive_target",
        "drive_remote",
        "drive_folder",
        "size_bytes",
        "uploaded_at",
    ):
        if item.get(optional_key) not in (None, ""):
            normalized[optional_key] = item[optional_key]
    return normalized


def write_catalog(images: list[dict[str, Any]]) -> None:
    IMAGE_CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    IMAGE_CATALOG_PATH.write_text(
        json.dumps(
            {
                "updated_at": now_iso(),
                "images": images,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def read_catalog() -> list[dict[str, Any]]:
    IMAGE_CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not IMAGE_CATALOG_PATH.exists():
        images = [normalize_image(item) for item in DEFAULT_IMAGES]
        write_catalog(images)
        return images

    try:
        raw = json.loads(IMAGE_CATALOG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        images = [normalize_image(item) for item in DEFAULT_IMAGES]
        write_catalog(images)
        return images

    raw_images = raw if isinstance(raw, list) else raw.get("images", [])
    images = [normalize_image(item) for item in raw_images]

    existing_ids = {item["id"] for item in images}
    changed = raw_images != images
    for default_item in DEFAULT_IMAGES:
        if default_item["id"] not in existing_ids:
            images.append(normalize_image(default_item))
            changed = True

    if changed:
        write_catalog(images)

    return images


def list_images(include_inactive: bool = False) -> list[dict[str, Any]]:
    images = read_catalog()
    if include_inactive:
        return images
    return [image for image in images if image.get("active", True)]


def image_details(image_id: str | None) -> dict[str, Any]:
    requested_id = image_id or "cirros"
    images = {image["id"]: image for image in list_images(include_inactive=True)}
    image = images.get(requested_id) or images.get("cirros") or normalize_image(DEFAULT_IMAGES[0])
    return image.copy()


def upsert_image(item: dict[str, Any]) -> dict[str, Any]:
    image = normalize_image(item)
    images = read_catalog()
    replaced = False

    for index, current in enumerate(images):
        if current["id"] == image["id"]:
            images[index] = image
            replaced = True
            break

    if not replaced:
        images.append(image)

    write_catalog(images)
    return image
