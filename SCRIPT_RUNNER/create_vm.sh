#!/bin/bash
set -euo pipefail

# Uso:
#   ./create_vm.sh <VM_NAME> <OVS_NAME> <VNC_PORT> [VLAN_1 ...]
#
# Crea una VM con QEMU directo, una interfaz TAP por VLAN y puertos OVS tagged.

VM_NAME="${1:-}"
OVS_NAME="${2:-}"
VNC_PORT="${3:-}"

if [ "$#" -lt 3 ] || [ -z "$VM_NAME" ] || [ -z "$OVS_NAME" ] || [ -z "$VNC_PORT" ]; then
    echo "Uso: $0 <VM_NAME> <OVS_NAME> <VNC_PORT> [VLAN_1 ...]"
    exit 1
fi

shift 3
VLANS=("$@")

BASE_IMAGE_URL="${NIMBUSCORE_BASE_IMAGE_URL:-https://download.cirros-cloud.net/0.6.2/cirros-0.6.2-x86_64-disk.img}"
BASE_IMAGE_DOWNLOAD_METHOD="${NIMBUSCORE_BASE_IMAGE_DOWNLOAD_METHOD:-auto}"
IMAGE_DIR="${NIMBUSCORE_QEMU_IMAGE_DIR:-/var/lib/qemu/images}"
OVS_UPLINKS="${NIMBUSCORE_OVS_UPLINKS:-}"
BASE_IMAGE_NAME_RAW="${NIMBUSCORE_BASE_IMAGE_NAME:-cirros-0.6.2}"
BASE_IMAGE_NAME="$(printf '%s' "$BASE_IMAGE_NAME_RAW" | tr -cs 'A-Za-z0-9._-' '-' | sed 's/^-//; s/-$//')"
if [[ "$BASE_IMAGE_NAME" == *.img || "$BASE_IMAGE_NAME" == *.qcow2 ]]; then
    DEFAULT_BASE_IMAGE_PATH="${IMAGE_DIR}/${BASE_IMAGE_NAME}"
else
    DEFAULT_BASE_IMAGE_PATH="${IMAGE_DIR}/${BASE_IMAGE_NAME}.qcow2"
fi
BASE_IMAGE_PATH="${NIMBUSCORE_BASE_IMAGE_PATH:-$DEFAULT_BASE_IMAGE_PATH}"
VM_DISK="${IMAGE_DIR}/${VM_NAME}.img"
PID_FILE="/var/run/qemu-${VM_NAME}.pid"
RAM_MB="${NIMBUSCORE_VM_RAM_MB:-512}"
VCPUS="${NIMBUSCORE_VM_VCPUS:-1}"
DISK_GB="${NIMBUSCORE_VM_DISK_GB:-1}"
MAC_SALT="${NIMBUSCORE_MAC_SALT:-nimbuscore}"
KEYPAIR_NAME_RAW="${NIMBUSCORE_KEYPAIR_NAME:-}"
KEYPAIR_NAME="$(printf '%s' "$KEYPAIR_NAME_RAW" | tr -cs 'A-Za-z0-9._-' '-' | sed 's/^-//; s/-$//')"
KEYPAIR_DIR="${NIMBUSCORE_KEYPAIR_DIR:-/home/ubuntu/nimbuscore-keys}"
PUBLIC_KEY_PATH="${NIMBUSCORE_PUBLIC_KEY_PATH:-}"
PUBLIC_KEY_B64="${NIMBUSCORE_PUBLIC_KEY_B64:-}"
CLOUD_INIT_DIR="${NIMBUSCORE_CLOUD_INIT_DIR:-/var/lib/qemu/cloud-init}"
REQUIRE_KEYPAIR="${NIMBUSCORE_REQUIRE_KEYPAIR:-false}"
CONSOLE_USER_RAW="${NIMBUSCORE_CONSOLE_USER:-nimbus}"
CONSOLE_USER="$(printf '%s' "$CONSOLE_USER_RAW" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9_-' '-' | sed 's/^-//; s/-$//')"
[ -z "$CONSOLE_USER" ] && CONSOLE_USER="nimbus"
CONSOLE_PASSWORD="${NIMBUSCORE_CONSOLE_PASSWORD:-NimbusCore123}"
ENABLE_PASSWORD_LOGIN="${NIMBUSCORE_ENABLE_PASSWORD_LOGIN:-true}"
if [ "$ENABLE_PASSWORD_LOGIN" != "true" ] && [ "$ENABLE_PASSWORD_LOGIN" != "false" ]; then
    ENABLE_PASSWORD_LOGIN="true"
fi

mac_for_iface() {
    local vm_name="$1"
    local iface_idx="$2"
    local vlan_id="$3"
    local hash

    hash="$(printf '%s' "${MAC_SALT}:${vm_name}:${iface_idx}:${vlan_id}" | sha1sum | cut -d' ' -f1)"
    printf '52:54:00:%s:%s:%s' "${hash:0:2}" "${hash:2:2}" "${hash:4:2}"
}

yaml_double_quote() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    printf '"%s"' "$value"
}

ensure_ovs_ready() {
    sudo ovs-vsctl --may-exist add-br "$OVS_NAME"
    sudo ip link set "$OVS_NAME" up

    for IFACE in $OVS_UPLINKS; do
        if ip link show "$IFACE" >/dev/null 2>&1; then
            sudo ip link set "$IFACE" up
            sudo ovs-vsctl --may-exist add-port "$OVS_NAME" "$IFACE"
        else
            echo "[create_vm] WARN: uplink OVS no existe en este host: $IFACE"
        fi
    done
}

create_cloud_init_seed() {
    local public_key_path="$PUBLIC_KEY_PATH"
    local public_key=""
    local tmp_seed
    local quoted_console_password

    if [ -n "$PUBLIC_KEY_B64" ]; then
        public_key="$(printf '%s' "$PUBLIC_KEY_B64" | base64 -d)"
    elif [ -z "$public_key_path" ] && [ -n "$KEYPAIR_NAME" ]; then
        public_key_path="${KEYPAIR_DIR}/${KEYPAIR_NAME}.pub"
    fi

    if [ -z "$public_key" ] && [ -n "$public_key_path" ] && [ ! -f "$public_key_path" ]; then
        echo "[create_vm] WARN: llave publica no encontrada: $public_key_path"
        if [ "$REQUIRE_KEYPAIR" = "true" ]; then
            echo "[create_vm] ERROR: NIMBUSCORE_REQUIRE_KEYPAIR=true y no existe la llave publica."
            exit 1
        fi
    fi

    if [ -z "$public_key" ] && [ -n "$public_key_path" ] && [ -f "$public_key_path" ]; then
        public_key="$(sudo awk 'NF {print; exit}' "$public_key_path")"
    fi

    if [ -z "$public_key" ] && [ "$REQUIRE_KEYPAIR" = "true" ]; then
        echo "[create_vm] ERROR: llave publica vacia: $public_key_path"
        exit 1
    fi

    sudo mkdir -p "$CLOUD_INIT_DIR"
    tmp_seed="$(mktemp -d)"

    quoted_console_password="$(yaml_double_quote "$CONSOLE_PASSWORD")"
    {
        printf '#cloud-config\n'
        printf 'hostname: %s\n' "$VM_NAME"
        printf 'manage_etc_hosts: true\n'
        printf 'ssh_pwauth: %s\n' "$ENABLE_PASSWORD_LOGIN"
        printf 'disable_root: false\n'
        printf 'users:\n'
        printf '  - default\n'
        printf '  - name: %s\n' "$CONSOLE_USER"
        printf '    gecos: NimbusCore Console User\n'
        printf '    groups: sudo\n'
        printf '    sudo: ALL=(ALL) NOPASSWD:ALL\n'
        printf '    shell: /bin/bash\n'
        printf '    lock_passwd: false\n'
        if [ -n "$public_key" ]; then
            printf '    ssh_authorized_keys:\n'
            printf '      - %s\n' "$public_key"
        fi
        printf 'chpasswd:\n'
        printf '  expire: false\n'
        printf '  users:\n'
        printf '    - name: %s\n' "$CONSOLE_USER"
        printf '      password: %s\n' "$quoted_console_password"
        printf '      type: text\n'
        if [ -n "$public_key" ]; then
            printf 'ssh_authorized_keys:\n'
            printf '  - %s\n' "$public_key"
        fi
    } > "${tmp_seed}/user-data"

    {
        printf 'version: 2\n'
        if [ "${#VLANS[@]}" -eq 0 ]; then
            printf 'ethernets: {}\n'
        else
            printf 'ethernets:\n'
        fi
        for idx in "${!VLANS[@]}"; do
            local vlan_id="${VLANS[$idx]}"
            local mac_addr
            mac_addr="$(mac_for_iface "$VM_NAME" "$idx" "$vlan_id")"
            printf '  eth%s:\n' "$idx"
            printf '    match:\n'
            printf '      macaddress: "%s"\n' "$mac_addr"
            printf '    set-name: eth%s\n' "$idx"
            printf '    dhcp4: true\n'
            printf '    dhcp6: false\n'
            printf '    optional: true\n'
        done
    } > "${tmp_seed}/network-config"

    cat > "${tmp_seed}/meta-data" <<EOF
instance-id: ${VM_NAME}
local-hostname: ${VM_NAME}
EOF

    SEED_ISO="${CLOUD_INIT_DIR}/${VM_NAME}-seed.iso"
    sudo rm -f "$SEED_ISO"

    if command -v cloud-localds >/dev/null 2>&1; then
        sudo cloud-localds --network-config="${tmp_seed}/network-config" \
            "$SEED_ISO" "${tmp_seed}/user-data" "${tmp_seed}/meta-data"
    elif command -v genisoimage >/dev/null 2>&1; then
        (
            cd "$tmp_seed"
            sudo genisoimage -quiet -output "$SEED_ISO" -volid cidata -joliet -rock user-data meta-data network-config
        )
    else
        rm -rf "$tmp_seed"
        echo "[create_vm] WARN: no existe cloud-localds ni genisoimage; la VM se creara sin cloud-init."
        if [ "$REQUIRE_KEYPAIR" = "true" ]; then
            echo "[create_vm] ERROR: instala cloud-image-utils o genisoimage para inyectar cloud-init."
            exit 1
        fi
        return 0
    fi

    sudo chmod 0644 "$SEED_ISO"
    rm -rf "$tmp_seed"
    CLOUD_INIT_ARGS=(-drive "file=${SEED_ISO},format=raw,media=cdrom,readonly=on")
    echo "[create_vm] Cloud-init seed creado: $SEED_ISO usuario_consola=$CONSOLE_USER ssh_password_login=$ENABLE_PASSWORD_LOGIN llave=${KEYPAIR_NAME:-none}"
}

download_base_image() {
    local tmp_image="$1"

    if command -v wget >/dev/null 2>&1; then
        if [ "$BASE_IMAGE_DOWNLOAD_METHOD" = "wget-no-check-certificate" ] || [[ "$BASE_IMAGE_URL" == *"drive.usercontent.google.com"* ]]; then
            sudo wget --no-check-certificate -q "$BASE_IMAGE_URL" -O "$tmp_image"
        else
            sudo wget -q "$BASE_IMAGE_URL" -O "$tmp_image"
        fi
    else
        if [ "$BASE_IMAGE_DOWNLOAD_METHOD" = "wget-no-check-certificate" ] || [[ "$BASE_IMAGE_URL" == *"drive.usercontent.google.com"* ]]; then
            sudo curl -k -fL "$BASE_IMAGE_URL" -o "$tmp_image"
        else
            sudo curl -fsSL "$BASE_IMAGE_URL" -o "$tmp_image"
        fi
    fi
}

if [ -f "$PID_FILE" ]; then
    PID="$(cat "$PID_FILE" || true)"
    if [ -n "$PID" ] && ps -p "$PID" >/dev/null 2>&1; then
        echo "[create_vm] La VM $VM_NAME ya esta corriendo con PID $PID."
        exit 0
    fi
    sudo rm -f "$PID_FILE"
fi

echo "[create_vm] VM=$VM_NAME VNC=$VNC_PORT OVS=$OVS_NAME vCPUs=$VCPUS RAM=${RAM_MB}MB DISK=${DISK_GB}GB VLANs=${VLANS[*]} KEYPAIR=${KEYPAIR_NAME:-none}"

sudo mkdir -p "$IMAGE_DIR"
ensure_ovs_ready
CLOUD_INIT_ARGS=()

if [ ! -f "$BASE_IMAGE_PATH" ]; then
    echo "[create_vm] Imagen base no encontrada. Descargando $BASE_IMAGE_URL metodo=$BASE_IMAGE_DOWNLOAD_METHOD"
    TMP_IMAGE="${BASE_IMAGE_PATH}.download"
    sudo rm -f "$TMP_IMAGE"
    download_base_image "$TMP_IMAGE"
    sudo mv "$TMP_IMAGE" "$BASE_IMAGE_PATH"
    sudo qemu-img info "$BASE_IMAGE_PATH" >/dev/null
else
    echo "[create_vm] Usando imagen base cacheada $BASE_IMAGE_PATH"
fi

if [ ! -f "$VM_DISK" ]; then
    sudo qemu-img create -f qcow2 -F qcow2 -b "$BASE_IMAGE_PATH" "$VM_DISK" "${DISK_GB}G"
fi

create_cloud_init_seed

NET_ARGS=()
for idx in "${!VLANS[@]}"; do
    VLAN_ID="${VLANS[$idx]}"
    TAP_IF="tap$(printf '%s' "${VM_NAME}-${idx}-${VLAN_ID}" | sha1sum | cut -c1-10)"
    MAC_ADDR="$(mac_for_iface "$VM_NAME" "$idx" "$VLAN_ID")"

    if ! ip link show "$TAP_IF" >/dev/null 2>&1; then
        sudo ip tuntap add dev "$TAP_IF" mode tap
    fi
    sudo ip link set "$TAP_IF" up
    sudo ovs-vsctl --may-exist add-port "$OVS_NAME" "$TAP_IF" -- \
        set port "$TAP_IF" tag="$VLAN_ID" external_ids:vm="$VM_NAME" \
        external_ids:vlan="$VLAN_ID" external_ids:mac="$MAC_ADDR"

    echo "[create_vm] NIC idx=$idx VLAN=$VLAN_ID TAP=$TAP_IF MAC=$MAC_ADDR"

    NET_ARGS+=(
        -netdev "tap,id=net${idx},ifname=${TAP_IF},script=no,downscript=no"
        -device "virtio-net-pci,netdev=net${idx},mac=${MAC_ADDR}"
    )
done

sudo qemu-system-x86_64 \
    -name "$VM_NAME" \
    -enable-kvm \
    -m "$RAM_MB" \
    -smp "$VCPUS" \
    -drive "file=${VM_DISK},format=qcow2" \
    "${CLOUD_INIT_ARGS[@]}" \
    "${NET_ARGS[@]}" \
    -vnc ":$((VNC_PORT - 5900))" \
    -daemonize \
    -pidfile "$PID_FILE"

echo "[create_vm] OK. VNC disponible en puerto $VNC_PORT"
