#!/bin/bash
set -euo pipefail

# Uso:
#   ./delete_network_vlan.sh <VLAN_ID> [OVS_NAME]
#
# Limpia los recursos creados por create_network_vlan.sh para una VLAN:
# namespace DHCP, dnsmasq, veths, puerto internal vlan<ID>, reglas FORWARD y archivos runtime.

VLAN_ID="${1:-}"
OVS_NAME="${2:-${NIMBUSCORE_OVS_NAME:-br-int}}"

if [ -z "$VLAN_ID" ]; then
    echo "Uso: $0 <VLAN_ID> [OVS_NAME]"
    exit 1
fi

IFACE_NAME="vlan${VLAN_ID}"
NS_NAME="dhcp-ns-vlan${VLAN_ID}"
VETH_HOST="veth-h-${VLAN_ID}"
VETH_NS="veth-ns-${VLAN_ID}"
DNSMASQ_PID="/var/run/dnsmasq-${NS_NAME}.pid"
DNSMASQ_LEASES="/var/lib/misc/dnsmasq-${NS_NAME}.leases"
DNSMASQ_LOG="/var/log/dnsmasq-${NS_NAME}.log"

echo "[delete_network_vlan] Limpiando VLAN=$VLAN_ID OVS=$OVS_NAME"

delete_forward_rules_for_iface() {
    local iface="$1"
    local rules

    rules="$(sudo iptables -S FORWARD 2>/dev/null | grep -E -- "( -i ${iface}( |$)| -o ${iface}( |$))" || true)"
    if [ -z "$rules" ]; then
        return 0
    fi

    while IFS= read -r rule; do
        [ -z "$rule" ] && continue
        sudo iptables ${rule/-A FORWARD/-D FORWARD} 2>/dev/null || true
    done <<EOF
$rules
EOF
}

if [ -f "$DNSMASQ_PID" ]; then
    DNSMASQ_PROCESS="$(cat "$DNSMASQ_PID" || true)"
    if [ -n "$DNSMASQ_PROCESS" ]; then
        sudo kill "$DNSMASQ_PROCESS" 2>/dev/null || true
    fi
fi

if sudo ip netns list | grep -q "^${NS_NAME}"; then
    for PID in $(sudo ip netns pids "$NS_NAME" 2>/dev/null || true); do
        sudo kill "$PID" 2>/dev/null || true
    done
fi

delete_forward_rules_for_iface "$IFACE_NAME"

sudo ovs-vsctl --if-exists del-port "$OVS_NAME" "$VETH_HOST" 2>/dev/null || true
sudo ovs-vsctl --if-exists del-port "$OVS_NAME" "$IFACE_NAME" 2>/dev/null || true

if ip link show "$VETH_HOST" >/dev/null 2>&1; then
    sudo ip link delete "$VETH_HOST" 2>/dev/null || true
fi

if ip link show "$IFACE_NAME" >/dev/null 2>&1; then
    sudo ip link delete "$IFACE_NAME" 2>/dev/null || true
fi

if sudo ip netns list | grep -q "^${NS_NAME}"; then
    sudo ip netns del "$NS_NAME" 2>/dev/null || true
fi

sudo rm -f "$DNSMASQ_PID" "$DNSMASQ_LEASES" "$DNSMASQ_LOG"

echo "[delete_network_vlan] OK"
