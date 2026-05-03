#!/bin/bash
set -euo pipefail

# Uso: ./create_network_vlan.sh <VLAN_ID> <CIDR> <dhcp|nodhcp> [DHCP_RANGE_START] [DHCP_RANGE_END]

VLAN_ID="${1:-}"
CIDR="${2:-}"
DHCP_FLAG="${3:-}"
DHCP_START="${4:-}"
DHCP_END="${5:-}"
OVS_NAME="${NIMBUSCORE_OVS_NAME:-br-int}"

if [ -z "$VLAN_ID" ] || [ -z "$CIDR" ] || [ -z "$DHCP_FLAG" ]; then
    echo "Uso: $0 <VLAN_ID> <CIDR> <dhcp|nodhcp> [DHCP_RANGE_START] [DHCP_RANGE_END]"
    exit 1
fi

if [ "$DHCP_FLAG" = "dhcp" ] && { [ -z "$DHCP_START" ] || [ -z "$DHCP_END" ]; }; then
    echo "Error: Si se habilita DHCP debe indicar el rango de direcciones."
    exit 1
fi

NETWORK="${CIDR%/*}"
PREFIX="${CIDR#*/}"
IFS='.' read -r O1 O2 O3 O4 <<< "$NETWORK"

GW_IP="$O1.$O2.$O3.$((O4 + 1))"
DHCP_IP="$O1.$O2.$O3.$((O4 + 2))"

IFACE_NAME="vlan${VLAN_ID}"
NS_NAME="dhcp-ns-vlan${VLAN_ID}"
VETH_HOST="veth-h-${VLAN_ID}"
VETH_NS="veth-ns-${VLAN_ID}"
DNSMASQ_PID="/var/run/dnsmasq-${NS_NAME}.pid"

echo "[create_network_vlan] VLAN=$VLAN_ID CIDR=$CIDR GW=$GW_IP OVS=$OVS_NAME"

sudo ovs-vsctl --may-exist add-br "$OVS_NAME"
sudo ovs-vsctl --may-exist add-port "$OVS_NAME" "$IFACE_NAME" \
    -- set interface "$IFACE_NAME" type=internal \
    -- set port "$IFACE_NAME" tag="$VLAN_ID"
sudo ip link set "$IFACE_NAME" up
sudo ip addr replace "$GW_IP/$PREFIX" dev "$IFACE_NAME"

if [ "$DHCP_FLAG" = "dhcp" ]; then
    echo "[create_network_vlan] Configurando DHCP aislado en namespace $NS_NAME"

    if ! sudo ip netns list | grep -q "^${NS_NAME}"; then
        sudo ip netns add "$NS_NAME"
    fi

    if ! ip link show "$VETH_HOST" >/dev/null 2>&1 && \
       ! sudo ip netns exec "$NS_NAME" ip link show "$VETH_NS" >/dev/null 2>&1; then
        sudo ip link add "$VETH_HOST" type veth peer name "$VETH_NS"
    fi

    sudo ovs-vsctl --may-exist add-port "$OVS_NAME" "$VETH_HOST" -- set port "$VETH_HOST" tag="$VLAN_ID"
    sudo ip link set "$VETH_HOST" up

    if ip link show "$VETH_NS" >/dev/null 2>&1; then
        sudo ip link set "$VETH_NS" netns "$NS_NAME"
    fi

    sudo ip netns exec "$NS_NAME" ip link set lo up
    sudo ip netns exec "$NS_NAME" ip link set "$VETH_NS" up
    sudo ip netns exec "$NS_NAME" ip addr replace "$DHCP_IP/$PREFIX" dev "$VETH_NS"
    sudo ip netns exec "$NS_NAME" ip route replace default via "$GW_IP"

    if [ -f "$DNSMASQ_PID" ]; then
        OLD_PID="$(cat "$DNSMASQ_PID" || true)"
        if [ -n "$OLD_PID" ]; then
            sudo kill "$OLD_PID" 2>/dev/null || true
        fi
        sudo rm -f "$DNSMASQ_PID"
    fi

    if ! command -v dnsmasq >/dev/null 2>&1; then
        echo "[create_network_vlan] dnsmasq no esta instalado."
        exit 1
    fi

    sudo ip netns exec "$NS_NAME" dnsmasq \
        --conf-file=/dev/null \
        --interface="$VETH_NS" \
        --bind-interfaces \
        --dhcp-range="${DHCP_START},${DHCP_END},12h" \
        --dhcp-option=3,"$GW_IP" \
        --dhcp-option=6,8.8.8.8 \
        --no-resolv \
        --pid-file="$DNSMASQ_PID" \
        --log-facility="/var/log/dnsmasq-${NS_NAME}.log"
fi

echo "[create_network_vlan] OK"
