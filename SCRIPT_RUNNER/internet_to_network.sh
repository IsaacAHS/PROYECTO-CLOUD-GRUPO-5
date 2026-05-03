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

sudo iptables -C FORWARD -i "$IFACE_VLAN" -o "$IFACE_OUT" -s "$CIDR" -j ACCEPT 2>/dev/null || \
    sudo iptables -I FORWARD -i "$IFACE_VLAN" -o "$IFACE_OUT" -s "$CIDR" -j ACCEPT
sudo iptables -C FORWARD -i "$IFACE_OUT" -o "$IFACE_VLAN" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
    sudo iptables -I FORWARD -i "$IFACE_OUT" -o "$IFACE_VLAN" -m state --state RELATED,ESTABLISHED -j ACCEPT
sudo iptables -t nat -C POSTROUTING -s "$CIDR" -o "$IFACE_OUT" -j MASQUERADE 2>/dev/null || \
    sudo iptables -t nat -I POSTROUTING -s "$CIDR" -o "$IFACE_OUT" -j MASQUERADE

echo "[internet_to_network] OK via $IFACE_OUT"
