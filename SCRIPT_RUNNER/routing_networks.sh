#!/bin/bash
set -euo pipefail

VLAN_A="${1:-}"
VLAN_B="${2:-}"

if [ -z "$VLAN_A" ] || [ -z "$VLAN_B" ]; then
    echo "Uso: $0 <VLAN_A> <VLAN_B>"
    exit 1
fi

IF_A="vlan${VLAN_A}"
IF_B="vlan${VLAN_B}"

echo "[routing_networks] Habilitando ruteo ${IF_A} <-> ${IF_B}"

echo 1 | sudo tee /proc/sys/net/ipv4/ip_forward >/dev/null
sudo iptables -C FORWARD -i "$IF_A" -o "$IF_B" -j ACCEPT 2>/dev/null || \
    sudo iptables -A FORWARD -i "$IF_A" -o "$IF_B" -j ACCEPT
sudo iptables -C FORWARD -i "$IF_B" -o "$IF_A" -j ACCEPT 2>/dev/null || \
    sudo iptables -A FORWARD -i "$IF_B" -o "$IF_A" -j ACCEPT

echo "[routing_networks] OK"
