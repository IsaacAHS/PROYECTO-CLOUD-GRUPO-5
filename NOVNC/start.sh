#!/bin/sh
set -eu

TOKEN_FILE="${NOVNC_TOKEN_FILE:-/tokens/tokens.cfg}"
NOVNC_WEB_DIR="${NOVNC_WEB_DIR:-/usr/share/novnc}"

mkdir -p "$(dirname "$TOKEN_FILE")"
touch "$TOKEN_FILE"

echo "[novnc] token_file=$TOKEN_FILE web_dir=$NOVNC_WEB_DIR listen=0.0.0.0:6080"

exec websockify \
  --web="$NOVNC_WEB_DIR" \
  --token-plugin=TokenFile \
  --token-source="$TOKEN_FILE" \
  0.0.0.0:6080
