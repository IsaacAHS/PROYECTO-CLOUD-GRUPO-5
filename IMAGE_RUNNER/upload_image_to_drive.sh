#!/bin/sh
set -eu

SOURCE_FILE="${1:-}"
DEST_NAME="${2:-}"

if [ -z "$SOURCE_FILE" ] || [ -z "$DEST_NAME" ]; then
    echo "Uso: $0 <SOURCE_FILE> <DEST_NAME>" >&2
    exit 1
fi

if [ ! -f "$SOURCE_FILE" ]; then
    echo "Archivo no encontrado: $SOURCE_FILE" >&2
    exit 1
fi

if ! command -v rclone >/dev/null 2>&1; then
    echo "rclone no esta instalado en el contenedor backend." >&2
    exit 1
fi

REMOTE="${NIMBUSCORE_RCLONE_REMOTE:-}"
if [ -z "$REMOTE" ]; then
    REMOTE="$(rclone listremotes | sed 's/:$//' | head -n 1)"
fi

if [ -z "$REMOTE" ]; then
    echo "No se encontro ningun remote de rclone. Define NIMBUSCORE_RCLONE_REMOTE." >&2
    exit 1
fi

FOLDER="${NIMBUSCORE_RCLONE_FOLDER:-NimbusCore/images}"
DEST_NAME="$(printf '%s' "$DEST_NAME" | tr -cs 'A-Za-z0-9._-' '-' | sed 's/^-//; s/-$//')"
if [ -z "$DEST_NAME" ]; then
    echo "Nombre destino invalido." >&2
    exit 1
fi

if [ -n "$FOLDER" ]; then
    TARGET="${REMOTE}:${FOLDER}/${DEST_NAME}"
else
    TARGET="${REMOTE}:${DEST_NAME}"
fi

rclone copyto "$SOURCE_FILE" "$TARGET" --drive-acknowledge-abuse

PUBLIC_LINK="$(rclone link "$TARGET")"
STAT_JSON="$(rclone lsjson --stat "$TARGET")"

python3 - "$STAT_JSON" "$PUBLIC_LINK" "$TARGET" "$REMOTE" "$FOLDER" "$DEST_NAME" <<'PY'
import json
import re
import sys
from urllib.parse import parse_qs, urlparse

stat_raw, public_link, target, remote, folder, dest_name = sys.argv[1:7]
try:
    stat = json.loads(stat_raw)
except json.JSONDecodeError:
    stat = {}

file_id = stat.get("ID") or stat.get("Id") or ""
if not file_id:
    parsed = urlparse(public_link)
    qs = parse_qs(parsed.query)
    file_id = (qs.get("id") or [""])[0]
if not file_id:
    match = re.search(r"/d/([^/]+)", public_link)
    file_id = match.group(1) if match else ""

if not file_id:
    raise SystemExit("No se pudo obtener el file_id de Google Drive.")

download_url = (
    "https://drive.usercontent.google.com/download"
    f"?id={file_id}&export=download&confirm=t"
)

print(json.dumps({
    "file_id": file_id,
    "name": dest_name,
    "remote": remote,
    "folder": folder,
    "target": target,
    "public_link": public_link,
    "download_url": download_url,
    "download_method": "wget-no-check-certificate",
    "size_bytes": stat.get("Size"),
}, ensure_ascii=True))
PY
