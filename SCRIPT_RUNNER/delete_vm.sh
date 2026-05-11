#!/bin/bash
set -euo pipefail

VM_NAME="${1:-}"
OVS_NAME="${2:-${NIMBUSCORE_OVS_NAME:-br-int}}"

if [ -z "$VM_NAME" ]; then
    echo "Uso: $0 <VM_NAME> [OVS_NAME]"
    exit 1
fi

IMAGE_DIR="${NIMBUSCORE_QEMU_IMAGE_DIR:-/var/lib/qemu/images}"
CLOUD_INIT_DIR="${NIMBUSCORE_CLOUD_INIT_DIR:-/var/lib/qemu/cloud-init}"
VM_DISK="${IMAGE_DIR}/${VM_NAME}.img"
PID_FILE="/var/run/qemu-${VM_NAME}.pid"
SEED_ISO="${CLOUD_INIT_DIR}/${VM_NAME}-seed.iso"

echo "[delete_vm] Eliminando $VM_NAME"

if [ -f "$PID_FILE" ]; then
    VM_PID="$(cat "$PID_FILE" || true)"
    if [ -n "$VM_PID" ]; then
        sudo kill "$VM_PID" 2>/dev/null || true
    fi
    sudo rm -f "$PID_FILE"
fi

for VM_PID in $(pgrep -f "qemu-system.*-name ${VM_NAME}([[:space:],]|$)" 2>/dev/null || true); do
    sudo kill "$VM_PID" 2>/dev/null || true
done

for TAP_IF in $(sudo ovs-vsctl --bare --columns=name find port external_ids:vm="$VM_NAME" 2>/dev/null || true); do
    sudo ovs-vsctl del-port "$OVS_NAME" "$TAP_IF" 2>/dev/null || true
    sudo ip link delete "$TAP_IF" 2>/dev/null || true
done

sudo rm -f "$VM_DISK"
sudo rm -f "$SEED_ISO"

echo "[delete_vm] OK"
