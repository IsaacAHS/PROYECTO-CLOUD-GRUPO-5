#!/bin/bash
# create_ring_topology.sh
# Crea una topología en ANILLO de N VMs en el clúster Linux.
# Cada enlace entre VM_i y VM_(i+1) usa una VLAN dedicada.
# El último enlace cierra el anillo: VM_N ── VM_1.
#
# Topología (N=4):
#
#   VM1 ──[VLAN_BASE]── VM2
#    │                   │
# [VLAN_BASE+3]    [VLAN_BASE+1]
#    │                   │
#   VM4 ──[VLAN_BASE+2]── VM3
#
# Uso:
#   ./create_ring_topology.sh <SLICE_NAME> <N_VMS> <VLAN_BASE> <VNC_BASE> <CIDR_BASE>
#
# Parámetros:
#   SLICE_NAME  : Nombre del slice (prefijo para todas las VMs y redes)
#   N_VMS       : Número de VMs en el anillo (mínimo 3)
#   VLAN_BASE   : VLAN ID de inicio (se usarán N VLANs: VLAN_BASE ... VLAN_BASE+N-1)
#   VNC_BASE    : Puerto VNC de inicio
#   CIDR_BASE   : Tercer octeto base para las subredes (ej: 20 → 192.168.20.0/24, ...)
#
# Ejemplo:
#   ./create_ring_topology.sh slice2 4 200 5910 20
#   Crea: slice2-vm1, slice2-vm2, slice2-vm3, slice2-vm4
#   VLANs: 200 (vm1-vm2), 201 (vm2-vm3), 202 (vm3-vm4), 203 (vm4-vm1)

# ──────────────────────────────────────────────
# Configuración del clúster
# ──────────────────────────────────────────────
HEADNODE_IP="${NIMBUSCORE_HEADNODE_IP:-10.0.10.4}"
IFS=',' read -r -a COMPUTE_IPS <<< "${NIMBUSCORE_COMPUTE_IPS:-10.0.10.1,10.0.10.2,10.0.10.3}"
OVS_NAME="${NIMBUSCORE_OVS_NAME:-br-int}"
OVS_UPLINKS="${NIMBUSCORE_OVS_UPLINKS:-ens4}"
SSH_USER="${NIMBUSCORE_SSH_USER:-ubuntu}"
SCRIPTS_DIR="${NIMBUSCORE_REMOTE_SCRIPTS_DIR:-/home/ubuntu/script_runner}"
SSH_OPTS="${NIMBUSCORE_SSH_OPTS:--o StrictHostKeyChecking=accept-new}"
HEADNODE_LOCAL="${NIMBUSCORE_HEADNODE_LOCAL:-true}"
KEYPAIR_DIR="${NIMBUSCORE_KEYPAIR_DIR:-/home/ubuntu/nimbuscore-keys}"
CONSOLE_USER="${NIMBUSCORE_CONSOLE_USER:-nimbus}"
CONSOLE_PASSWORD="${NIMBUSCORE_CONSOLE_PASSWORD:-NimbusCore123}"
ENABLE_PASSWORD_LOGIN="${NIMBUSCORE_ENABLE_PASSWORD_LOGIN:-true}"
DEFAULT_VM_SPEC="${NIMBUSCORE_DEFAULT_VM_SPEC:-1:2048:20}"
IFS=';' read -r -a VM_SPECS <<< "${NIMBUSCORE_TOPOLOGY_VM_SPECS:-}"
IFS=';' read -r -a IMAGE_SPECS <<< "${NIMBUSCORE_TOPOLOGY_IMAGE_SPECS:-}"
IFS=';' read -r -a KEYPAIR_SPECS <<< "${NIMBUSCORE_TOPOLOGY_KEYPAIR_SPECS:-}"

vm_spec_for_index() {
    local index="$1"
    local spec="${VM_SPECS[$index]:-$DEFAULT_VM_SPEC}"
    local vcpus ram_mb disk_gb
    IFS=':' read -r vcpus ram_mb disk_gb <<< "$spec"
    echo "${vcpus:-1} ${ram_mb:-2048} ${disk_gb:-20}"
}

image_spec_for_index() {
    local index="$1"
    local spec="${IMAGE_SPECS[$index]:-}"
    local image_name image_url download_method
    IFS='|' read -r image_name image_url download_method <<< "$spec"
    echo "${image_name:-cirros-0-6-2} ${image_url:-https://download.cirros-cloud.net/0.6.2/cirros-0.6.2-x86_64-disk.img} ${download_method:-auto}"
}

keypair_spec_for_index() {
    local index="$1"
    local keypair="${KEYPAIR_SPECS[$index]:-default-key}"
    echo "${keypair:-default-key}"
}

public_key_b64_for_keypair() {
    local keypair="$1"
    local public_key_path="${KEYPAIR_DIR}/${keypair}.pub"

    if [ -f "$public_key_path" ]; then
        base64 -w 0 "$public_key_path"
    else
        echo ""
    fi
}

run_headnode_script() {
    local script_name="$1"
    shift

    if [ "$HEADNODE_LOCAL" = "true" ]; then
        sudo env NIMBUSCORE_OVS_NAME="$OVS_NAME" NIMBUSCORE_OVS_UPLINKS="$OVS_UPLINKS" \
            bash "${SCRIPTS_DIR}/${script_name}" "$@"
    else
        ssh ${SSH_OPTS} ${SSH_USER}@${HEADNODE_IP} \
            "NIMBUSCORE_OVS_NAME='$OVS_NAME' NIMBUSCORE_OVS_UPLINKS='$OVS_UPLINKS' bash ${SCRIPTS_DIR}/${script_name} $*"
    fi
}

# ──────────────────────────────────────────────
# Lectura de parámetros
# ──────────────────────────────────────────────
SLICE_NAME="$1"
N_VMS="$2"
VLAN_BASE="$3"
VNC_BASE="$4"
CIDR_BASE="$5"

if [ -z "$SLICE_NAME" ] || [ -z "$N_VMS" ] || [ -z "$VLAN_BASE" ] || \
   [ -z "$VNC_BASE" ]   || [ -z "$CIDR_BASE" ]; then
    echo "Uso: $0 <SLICE_NAME> <N_VMS> <VLAN_BASE> <VNC_BASE> <CIDR_BASE>"
    echo "Ejemplo: $0 slice2 4 200 5910 20"
    exit 1
fi

if [ "$N_VMS" -lt 3 ]; then
    echo "Error: Se necesitan al menos 3 VMs para una topología en anillo."
    exit 1
fi

N_COMPUTE=${#COMPUTE_IPS[@]}

echo "======================================================"
echo " Creando topología en ANILLO: $SLICE_NAME"
echo " VMs      : $N_VMS"
echo " VLANs    : $VLAN_BASE → $(( VLAN_BASE + N_VMS - 1 ))"
echo " VNC      : $VNC_BASE → $(( VNC_BASE + N_VMS - 1 ))"
echo "======================================================"

# ──────────────────────────────────────────────
# PASO 1: Crear N VLANs en el headnode
# Enlace k conecta VM_(k) con VM_(k+1 mod N)
# ──────────────────────────────────────────────
echo ""
echo "[PASO 1] Creando redes VLAN en el headnode ($HEADNODE_IP)..."

for (( k=0; k<N_VMS; k++ )); do
    VLAN_ID=$(( VLAN_BASE + k ))
    CIDR_THIRD=$(( CIDR_BASE + k ))
    CIDR="192.168.${CIDR_THIRD}.0/24"
    DHCP_START="192.168.${CIDR_THIRD}.10"
    DHCP_END="192.168.${CIDR_THIRD}.100"

    VM_A=$(( k + 1 ))
    VM_B=$(( (k + 1) % N_VMS + 1 ))
    echo "  → Enlace vm${VM_A}↔vm${VM_B} : VLAN $VLAN_ID | Red $CIDR"

    run_headnode_script create_network_vlan.sh "$VLAN_ID" "$CIDR" dhcp "$DHCP_START" "$DHCP_END"

    if [ $? -ne 0 ]; then
        echo "  [ERROR] Fallo al crear VLAN $VLAN_ID. Abortando."
        exit 1
    fi
done

# ──────────────────────────────────────────────
# PASO 2: Crear las VMs con sus dos interfaces TAP
#
# Cada VM_i pertenece a exactamente dos VLANs:
#   - VLAN "izquierda": enlace entre VM_(i-1) y VM_i  → VLAN_BASE + (i-1) mod N
#   - VLAN "derecha":   enlace entre VM_i y VM_(i+1)  → VLAN_BASE + i mod N
# ──────────────────────────────────────────────
echo ""
echo "[PASO 2] Creando VMs en servidores de cómputo..."

for (( i=0; i<N_VMS; i++ )); do
    VM_IDX=$(( i + 1 ))
    VM_NAME="${SLICE_NAME}-vm${VM_IDX}"
    VNC_PORT=$(( VNC_BASE + i ))
    COMPUTE_IP="${COMPUTE_IPS[$((i % N_COMPUTE))]}"

    # VLAN del enlace derecho (VM_i → VM_(i+1)): es la interfaz principal (primera TAP)
    VLAN_RIGHT=$(( VLAN_BASE + i % N_VMS ))
    # VLAN del enlace izquierdo (VM_(i-1) → VM_i): segunda interfaz TAP
    VLAN_LEFT=$(( VLAN_BASE + (i - 1 + N_VMS) % N_VMS ))

    echo "  → VM $VM_IDX/$N_VMS : $VM_NAME | servidor $COMPUTE_IP | VNC $VNC_PORT"
    echo "    VLANs: izq=$VLAN_LEFT  der=$VLAN_RIGHT"
    read -r VM_VCPUS VM_RAM_MB VM_DISK_GB <<< "$(vm_spec_for_index "$i")"
    read -r VM_IMAGE_NAME VM_IMAGE_URL VM_IMAGE_DOWNLOAD_METHOD <<< "$(image_spec_for_index "$i")"
    VM_KEYPAIR_NAME="$(keypair_spec_for_index "$i")"
    VM_PUBLIC_KEY_B64="$(public_key_b64_for_keypair "$VM_KEYPAIR_NAME")"
    echo "    Flavor efectivo: ${VM_VCPUS} vCPU | ${VM_RAM_MB} MB RAM | ${VM_DISK_GB} GB disco"
    echo "    Imagen: ${VM_IMAGE_NAME} (${VM_IMAGE_URL})"
    echo "    Par de llaves: ${VM_KEYPAIR_NAME}"

    ssh ${SSH_OPTS} ${SSH_USER}@${COMPUTE_IP} \
        "NIMBUSCORE_OVS_UPLINKS='$OVS_UPLINKS' NIMBUSCORE_VM_VCPUS=$VM_VCPUS NIMBUSCORE_VM_RAM_MB=$VM_RAM_MB NIMBUSCORE_VM_DISK_GB=$VM_DISK_GB NIMBUSCORE_BASE_IMAGE_NAME='$VM_IMAGE_NAME' NIMBUSCORE_BASE_IMAGE_URL='$VM_IMAGE_URL' NIMBUSCORE_BASE_IMAGE_DOWNLOAD_METHOD='$VM_IMAGE_DOWNLOAD_METHOD' NIMBUSCORE_CONSOLE_USER='$CONSOLE_USER' NIMBUSCORE_CONSOLE_PASSWORD='$CONSOLE_PASSWORD' NIMBUSCORE_ENABLE_PASSWORD_LOGIN='$ENABLE_PASSWORD_LOGIN' NIMBUSCORE_KEYPAIR_NAME='$VM_KEYPAIR_NAME' NIMBUSCORE_PUBLIC_KEY_B64='$VM_PUBLIC_KEY_B64' bash ${SCRIPTS_DIR}/create_vm.sh $VM_NAME $OVS_NAME $VNC_PORT $VLAN_RIGHT $VLAN_LEFT"

    if [ $? -ne 0 ]; then
        echo "  [ERROR] Fallo al crear VM $VM_NAME. Abortando."
        exit 1
    fi
done

# ──────────────────────────────────────────────
# PASO 3: Habilitar ruteo entre todas las VLANs
# del anillo (cada VLAN con sus dos vecinas)
# ──────────────────────────────────────────────
echo ""
echo "[PASO 3] Habilitando ruteo entre VLANs adyacentes en headnode..."

for (( k=0; k<N_VMS; k++ )); do
    VLAN_A=$(( VLAN_BASE + k ))
    VLAN_B=$(( VLAN_BASE + (k + 1) % N_VMS ))
    echo "  → Ruteo VLAN $VLAN_A ↔ VLAN $VLAN_B"
    run_headnode_script routing_networks.sh "$VLAN_A" "$VLAN_B"
done

# ──────────────────────────────────────────────
# Resumen final
# ──────────────────────────────────────────────
echo ""
echo "======================================================"
echo " Topología en ANILLO '$SLICE_NAME' creada exitosamente."
echo ""
echo " Nodos y acceso VNC:"
for (( i=0; i<N_VMS; i++ )); do
    VM_IDX=$(( i + 1 ))
    VM_NAME="${SLICE_NAME}-vm${VM_IDX}"
    VNC_PORT=$(( VNC_BASE + i ))
    COMPUTE_IP="${COMPUTE_IPS[$((i % N_COMPUTE))]}"
    echo "   $VM_NAME  →  $COMPUTE_IP  |  VNC: $COMPUTE_IP:$VNC_PORT"
done
echo ""
echo " Anillo de VLANs:"
for (( k=0; k<N_VMS; k++ )); do
    VM_A=$(( k + 1 ))
    VM_B=$(( k % N_VMS + 2 ))
    [ $VM_B -gt $N_VMS ] && VM_B=1
    C=$(( CIDR_BASE + k ))
    V=$(( VLAN_BASE + k ))
    echo "   vm${VM_A} ── VLAN $V (192.168.$C.0/24) ── vm${VM_B}"
done
echo "======================================================"
