#!/bin/bash
set -euo pipefail

# Uso:
#   ./create_vm.sh <VM_NAME> <OVS_NAME> <VNC_PORT> <VLAN_1> [VLAN_2 ...]
#
# Crea una VM con QEMU directo, una interfaz TAP por VLAN y puertos OVS tagged.

VM_NAME="${1:-}"
OVS_NAME="${2:-}"
VNC_PORT="${3:-}"

if [ "$#" -lt 4 ] || [ -z "$VM_NAME" ] || [ -z "$OVS_NAME" ] || [ -z "$VNC_PORT" ]; then
    echo "Uso: $0 <VM_NAME> <OVS_NAME> <VNC_PORT> <VLAN_1> [VLAN_2 ...]"
    exit 1
fi

shift 3
VLANS=("$@")

BASE_IMAGE_URL="${NIMBUSCORE_BASE_IMAGE_URL:-https://download.cirros-cloud.net/0.6.2/cirros-0.6.2-x86_64-disk.img}"
IMAGE_DIR="${NIMBUSCORE_QEMU_IMAGE_DIR:-/var/lib/qemu/images}"
BASE_IMAGE_PATH="${NIMBUSCORE_BASE_IMAGE_PATH:-${IMAGE_DIR}/cirros-base.img}"
VM_DISK="${IMAGE_DIR}/${VM_NAME}.img"
PID_FILE="/var/run/qemu-${VM_NAME}.pid"
RAM_MB="${NIMBUSCORE_VM_RAM_MB:-512}"
VCPUS="${NIMBUSCORE_VM_VCPUS:-1}"

if [ -f "$PID_FILE" ]; then
    PID="$(cat "$PID_FILE" || true)"
    if [ -n "$PID" ] && ps -p "$PID" >/dev/null 2>&1; then
        echo "[create_vm] La VM $VM_NAME ya esta corriendo con PID $PID."
        exit 0
    fi
    sudo rm -f "$PID_FILE"
fi

echo "[create_vm] VM=$VM_NAME VNC=$VNC_PORT OVS=$OVS_NAME VLANs=${VLANS[*]}"

sudo mkdir -p "$IMAGE_DIR"
sudo ovs-vsctl --may-exist add-br "$OVS_NAME"

if [ ! -f "$BASE_IMAGE_PATH" ]; then
    echo "[create_vm] Imagen base no encontrada. Descargando $BASE_IMAGE_URL"
    if command -v wget >/dev/null 2>&1; then
        sudo wget -q "$BASE_IMAGE_URL" -O "$BASE_IMAGE_PATH"
    else
        sudo curl -fsSL "$BASE_IMAGE_URL" -o "$BASE_IMAGE_PATH"
    fi
fi

if [ ! -f "$VM_DISK" ]; then
    sudo qemu-img create -f qcow2 -F qcow2 -b "$BASE_IMAGE_PATH" "$VM_DISK"
fi

NET_ARGS=()
for idx in "${!VLANS[@]}"; do
    VLAN_ID="${VLANS[$idx]}"
    TAP_IF="tap$(printf '%s' "${VM_NAME}-${idx}-${VLAN_ID}" | sha1sum | cut -c1-10)"

    if ! ip link show "$TAP_IF" >/dev/null 2>&1; then
        sudo ip tuntap add dev "$TAP_IF" mode tap
    fi
    sudo ip link set "$TAP_IF" up
    sudo ovs-vsctl --may-exist add-port "$OVS_NAME" "$TAP_IF" -- \
        set port "$TAP_IF" tag="$VLAN_ID" external_ids:vm="$VM_NAME"

    NET_ARGS+=(
        -netdev "tap,id=net${idx},ifname=${TAP_IF},script=no,downscript=no"
        -device "virtio-net-pci,netdev=net${idx}"
    )
done

sudo qemu-system-x86_64 \
    -name "$VM_NAME" \
    -enable-kvm \
    -m "$RAM_MB" \
    -smp "$VCPUS" \
    -drive "file=${VM_DISK},format=qcow2" \
    "${NET_ARGS[@]}" \
    -vnc ":$((VNC_PORT - 5900))" \
    -daemonize \
    -pidfile "$PID_FILE"

echo "[create_vm] OK. VNC disponible en puerto $VNC_PORT"
