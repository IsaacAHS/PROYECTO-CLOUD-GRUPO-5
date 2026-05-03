#!/bin/bash
set -euo pipefail

INTERFACES="$@"
OVS_NAME="${NIMBUSCORE_OVS_NAME:-br-int}"

if [ -z "$INTERFACES" ]; then
    echo "Uso: $0 <InterfacesAConectar>"
    echo "Ejemplo: $0 ens4 ens5"
    echo "No pases ens3 si es tu interfaz de gestion."
    exit 1
fi

sudo ovs-vsctl --may-exist add-br "$OVS_NAME"

for IFACE in $INTERFACES; do
    sudo ovs-vsctl --may-exist add-port "$OVS_NAME" "$IFACE"
done

sudo sysctl -w net.ipv4.ip_forward=1
if ! grep -q "^net.ipv4.ip_forward=1" /etc/sysctl.conf; then
    echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf >/dev/null
fi
sudo iptables -P FORWARD DROP

echo "[headnode_init] OK"
