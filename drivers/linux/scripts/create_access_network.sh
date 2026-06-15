#!/bin/bash
set -euo pipefail

# Crea o actualiza la red de gestion/acceso compartida.
#
# Esta red no representa enlaces de topologia. Solo agrega un servicio DHCP
# en un namespace conectado a OVS por la VLAN de gestion.

OVS_NAME="${NIMBUSCORE_OVS_NAME:-br-int}"
OVS_UPLINKS="${NIMBUSCORE_OVS_UPLINKS:-ens4}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${SCRIPT_DIR}/ovs_common.sh" ]; then
    # shellcheck source=/dev/null
    source "${SCRIPT_DIR}/ovs_common.sh"
fi
MGMT_VLAN="${NIMBUSCORE_MGMT_VLAN:-99}"
MGMT_CIDR="${NIMBUSCORE_MGMT_CIDR:-10.60.9.0/24}"
MGMT_GATEWAY="${NIMBUSCORE_MGMT_GATEWAY:-10.60.9.1}"
MGMT_DNS="${NIMBUSCORE_MGMT_DNS:-8.8.8.8}"
MGMT_DHCP_SERVER_IP="${NIMBUSCORE_MGMT_DHCP_SERVER_IP:-10.60.9.2}"
MGMT_DHCP_START="${NIMBUSCORE_MGMT_DHCP_START:-10.60.9.20}"
MGMT_DHCP_END="${NIMBUSCORE_MGMT_DHCP_END:-10.60.9.250}"
MGMT_DHCP_LEASE_TIME="${NIMBUSCORE_MGMT_DHCP_LEASE_TIME:-12h}"

PREFIX="${MGMT_CIDR#*/}"
if [ "$PREFIX" = "$MGMT_CIDR" ] || ! [[ "$PREFIX" =~ ^[0-9]+$ ]]; then
    PREFIX="24"
fi

NS_NAME="dhcp-ns-mgmt${MGMT_VLAN}"
VETH_HOST="veth-mg-${MGMT_VLAN}"
VETH_NS="veth-ns-${MGMT_VLAN}"
DNSMASQ_PID="/var/run/dnsmasq-${NS_NAME}.pid"
DNSMASQ_LEASES="/var/lib/misc/dnsmasq-${NS_NAME}.leases"
DNSMASQ_LOG="/var/log/dnsmasq-${NS_NAME}.log"
LEGACY_HOSTS_FILE="/var/lib/misc/nimbuscore-mgmt-vlan${MGMT_VLAN}.hosts"

echo "[create_access_network] VLAN=$MGMT_VLAN CIDR=$MGMT_CIDR DHCP=${MGMT_DHCP_START}-${MGMT_DHCP_END} GW=$MGMT_GATEWAY OVS=$OVS_NAME"

cleanup_legacy_vlan_dhcp() {
    local legacy_ns="dhcp-ns-vlan${MGMT_VLAN}"
    local legacy_iface="vlan${MGMT_VLAN}"
    local legacy_veth_host="veth-h-${MGMT_VLAN}"
    local legacy_pid="/var/run/dnsmasq-${legacy_ns}.pid"

    if [ -f "$legacy_pid" ]; then
        OLD_PID="$(cat "$legacy_pid" || true)"
        if [ -n "$OLD_PID" ]; then
            sudo kill "$OLD_PID" 2>/dev/null || true
        fi
        sudo rm -f "$legacy_pid"
    fi

    if sudo ip netns list | grep -q "^${legacy_ns}"; then
        for PID in $(sudo ip netns pids "$legacy_ns" 2>/dev/null || true); do
            sudo kill "$PID" 2>/dev/null || true
        done
        sudo ip netns del "$legacy_ns" 2>/dev/null || true
    fi

    sudo ovs-vsctl --if-exists del-port "$OVS_NAME" "$legacy_veth_host" 2>/dev/null || true
    sudo ovs-vsctl --if-exists del-port "$OVS_NAME" "$legacy_iface" 2>/dev/null || true
    sudo ip link delete "$legacy_veth_host" 2>/dev/null || true
    sudo ip link delete "$legacy_iface" 2>/dev/null || true
    sudo rm -f "/var/lib/misc/dnsmasq-${legacy_ns}.leases" "/var/log/dnsmasq-${legacy_ns}.log" "$LEGACY_HOSTS_FILE"
}

ensure_ovs_ready() {
    if declare -F nimbuscore_ensure_ovs_ready >/dev/null 2>&1; then
        nimbuscore_ensure_ovs_ready "$OVS_NAME" "$OVS_UPLINKS" "[create_access_network]"
        return
    fi
    sudo ovs-vsctl --may-exist add-br "$OVS_NAME"
    sudo ip link set "$OVS_NAME" up
}

restart_dnsmasq() {
    if [ -f "$DNSMASQ_PID" ]; then
        OLD_PID="$(cat "$DNSMASQ_PID" || true)"
        if [ -n "$OLD_PID" ]; then
            sudo kill "$OLD_PID" 2>/dev/null || true
        fi
        sudo rm -f "$DNSMASQ_PID"
    fi

    if ! command -v dnsmasq >/dev/null 2>&1; then
        echo "[create_access_network] ERROR: dnsmasq no esta instalado en el headnode."
        exit 1
    fi

    sudo ip netns exec "$NS_NAME" dnsmasq \
        --conf-file=/dev/null \
        --interface="$VETH_NS" \
        --bind-interfaces \
        --dhcp-authoritative \
        --dhcp-range="${MGMT_DHCP_START},${MGMT_DHCP_END},${MGMT_DHCP_LEASE_TIME}" \
        --dhcp-option=3,"$MGMT_GATEWAY" \
        --dhcp-option=6,"$MGMT_DNS" \
        --no-resolv \
        --pid-file="$DNSMASQ_PID" \
        --dhcp-leasefile="$DNSMASQ_LEASES" \
        --log-dhcp \
        --log-facility="$DNSMASQ_LOG"
}

cleanup_legacy_vlan_dhcp
ensure_ovs_ready

if ! sudo ip netns list | grep -q "^${NS_NAME}"; then
    sudo ip netns add "$NS_NAME"
fi

if ! ip link show "$VETH_HOST" >/dev/null 2>&1 && \
   ! sudo ip netns exec "$NS_NAME" ip link show "$VETH_NS" >/dev/null 2>&1; then
    sudo ip link add "$VETH_HOST" type veth peer name "$VETH_NS"
fi

sudo ovs-vsctl --may-exist add-port "$OVS_NAME" "$VETH_HOST" -- set port "$VETH_HOST" tag="$MGMT_VLAN"
sudo ip link set "$VETH_HOST" up

if ip link show "$VETH_NS" >/dev/null 2>&1; then
    sudo ip link set "$VETH_NS" netns "$NS_NAME"
fi

sudo ip netns exec "$NS_NAME" ip link set lo up
sudo ip netns exec "$NS_NAME" ip link set "$VETH_NS" up
sudo ip netns exec "$NS_NAME" ip addr replace "${MGMT_DHCP_SERVER_IP}/${PREFIX}" dev "$VETH_NS"

sudo rm -f "$LEGACY_HOSTS_FILE"
restart_dnsmasq

echo "[create_access_network] OK namespace=$NS_NAME iface=$VETH_NS mode=dynamic-dhcp"
