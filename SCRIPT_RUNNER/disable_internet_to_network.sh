#!/bin/bash
set -euo pipefail

VLAN_ID="${1:-}"
CIDR="${2:-}"
IFACE_OUT="${NIMBUSCORE_INTERNET_IFACE:-ens3}"

if [ -z "$VLAN_ID" ] || [ -z "$CIDR" ]; then
    echo "Uso: $0 <VLAN_ID> <CIDR>"
    exit 1
fi

IFACE_VLAN="vlan${VLAN_ID}"

sudo iptables -D FORWARD -i "$IFACE_VLAN" -o "$IFACE_OUT" -s "$CIDR" -j ACCEPT 2>/dev/null || true
sudo iptables -D FORWARD -i "$IFACE_OUT" -o "$IFACE_VLAN" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
sudo iptables -t nat -D POSTROUTING -s "$CIDR" -o "$IFACE_OUT" -j MASQUERADE 2>/dev/null || true

echo "[disable_internet_to_network] OK"
