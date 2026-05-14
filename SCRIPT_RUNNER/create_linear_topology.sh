#!/bin/bash
# create_linear_topology.sh
# Crea una topología LINEAL de N VMs en el clúster Linux.
# Cada enlace entre VM_i y VM_(i+1) usa una VLAN dedicada.
#
# Topología (N=4):
#   VM1 ---[VLAN_BASE]--- VM2 ---[VLAN_BASE+1]--- VM3 ---[VLAN_BASE+2]--- VM4
#
# Uso:
#   ./create_linear_topology.sh <SLICE_NAME> <N_VMS> <VLAN_BASE> <VNC_BASE> <CIDR_BASE>
#
# Parámetros:
#   SLICE_NAME  : Nombre del slice (prefijo para todas las VMs y redes)
#   N_VMS       : Número de VMs en la cadena lineal (mínimo 2)
#   VLAN_BASE   : VLAN ID de inicio (se usarán VLAN_BASE, VLAN_BASE+1, ..., VLAN_BASE+N-2)
#   VNC_BASE    : Puerto VNC de inicio (se usarán VNC_BASE, VNC_BASE+1, ..., VNC_BASE+N-1)
#   CIDR_BASE   : Tercer octeto base para las subredes (ej: 10 → 192.168.10.0/24, 192.168.11.0/24, ...)
#
# Ejemplo:
#   ./create_linear_topology.sh slice1 4 100 5901 10
#   Crea: slice1-vm1, slice1-vm2, slice1-vm3, slice1-vm4
#   VLANs: 100 (vm1-vm2), 101 (vm2-vm3), 102 (vm3-vm4)
#   Redes: 192.168.10.0/24, 192.168.11.0/24, 192.168.12.0/24

# ──────────────────────────────────────────────
# Configuración del clúster
# ──────────────────────────────────────────────
HEADNODE_IP="${NIMBUSCORE_HEADNODE_IP:-10.0.10.4}"        # IP de server4 (headnode) en la red de acceso
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
ENABLE_AUTO_ROUTING="${NIMBUSCORE_ENABLE_AUTO_ROUTING:-false}"
DEFAULT_VM_SPEC="${NIMBUSCORE_DEFAULT_VM_SPEC:-1:2048:1}"
IFS=';' read -r -a VM_SPECS <<< "${NIMBUSCORE_TOPOLOGY_VM_SPECS:-}"
IFS=';' read -r -a IMAGE_SPECS <<< "${NIMBUSCORE_TOPOLOGY_IMAGE_SPECS:-}"
IFS=';' read -r -a KEYPAIR_SPECS <<< "${NIMBUSCORE_TOPOLOGY_KEYPAIR_SPECS:-}"

vm_spec_for_index() {
    local index="$1"
    local spec="${VM_SPECS[$index]:-$DEFAULT_VM_SPEC}"
    local vcpus ram_mb disk_gb
    IFS=':' read -r vcpus ram_mb disk_gb <<< "$spec"
    echo "${vcpus:-1} ${ram_mb:-2048} ${disk_gb:-1}"
}

image_spec_for_index() {
    local index="$1"
    local spec="${IMAGE_SPECS[$index]:-}"
    local image_name image_url download_method cloud_init
    IFS='|' read -r image_name image_url download_method cloud_init <<< "$spec"
    echo "${image_name:-cirros-0-6-2} ${image_url:-https://download.cirros-cloud.net/0.6.2/cirros-0.6.2-x86_64-disk.img} ${download_method:-auto} ${cloud_init:-false}"
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
    echo "Ejemplo: $0 slice1 4 100 5901 10"
    exit 1
fi

if [ "$N_VMS" -lt 2 ]; then
    echo "Error: Se necesitan al menos 2 VMs para una topología lineal."
    exit 1
fi

N_LINKS=$(( N_VMS - 1 ))   # Número de enlaces = N_VMS - 1
N_COMPUTE=${#COMPUTE_IPS[@]}

echo "======================================================"
echo " Creando topología LINEAL: $SLICE_NAME"
echo " VMs      : $N_VMS"
echo " VLANs    : $VLAN_BASE → $(( VLAN_BASE + N_LINKS - 1 ))"
echo " VNC      : $VNC_BASE → $(( VNC_BASE + N_VMS - 1 ))"
echo "======================================================"

# ──────────────────────────────────────────────
# PASO 1: Crear las redes VLAN en el headnode
# (una VLAN por cada enlace: N_VMS - 1 VLANs)
# ──────────────────────────────────────────────
echo ""
echo "[PASO 1] Creando redes VLAN en el headnode ($HEADNODE_IP)..."

for (( link=0; link<N_LINKS; link++ )); do
    VLAN_ID=$(( VLAN_BASE + link ))
    CIDR_THIRD=$(( CIDR_BASE + link ))
    CIDR="192.168.${CIDR_THIRD}.0/24"
    DHCP_START="192.168.${CIDR_THIRD}.10"
    DHCP_END="192.168.${CIDR_THIRD}.100"

    echo "  → Enlace $((link+1))/$N_LINKS : VLAN $VLAN_ID | Red $CIDR"
    run_headnode_script create_network_vlan.sh "$VLAN_ID" "$CIDR" dhcp "$DHCP_START" "$DHCP_END"

    if [ $? -ne 0 ]; then
        echo "  [ERROR] Fallo al crear VLAN $VLAN_ID en headnode. Abortando."
        exit 1
    fi
done

# ──────────────────────────────────────────────
# PASO 2: Crear las VMs en los servidores de cómputo
# Asignación round-robin entre COMPUTE_IPS
# Cada VM que NO es extremo tiene DOS interfaces TAP (una por cada enlace adyacente)
#
# Estructura de VLANs por VM en lineal:
#   VM_1    : solo VLAN_(base+0)            [extremo izquierdo]
#   VM_i    : VLAN_(base+i-2) y VLAN_(base+i-1)  [nodo intermedio]
#   VM_N    : solo VLAN_(base+N-2)          [extremo derecho]
# ──────────────────────────────────────────────
echo ""
echo "[PASO 2] Creando VMs en servidores de cómputo..."

for (( i=0; i<N_VMS; i++ )); do
    VM_IDX=$(( i + 1 ))
    VM_NAME="${SLICE_NAME}-vm${VM_IDX}"
    VNC_PORT=$(( VNC_BASE + i ))
    COMPUTE_IP="${COMPUTE_IPS[$((i % N_COMPUTE))]}"

    # VLANs a las que pertenece esta VM:
    #   - enlace izquierdo: VLAN_BASE + (i-1)   (si i > 0)
    #   - enlace derecho:   VLAN_BASE + i         (si i < N_VMS-1)
    LEFT_VLAN=""
    RIGHT_VLAN=""
    [ $i -gt 0 ]           && LEFT_VLAN=$(( VLAN_BASE + i - 1 ))
    [ "$i" -lt "$((N_VMS-1))" ] && RIGHT_VLAN=$(( VLAN_BASE + i ))

    echo "  → VM $VM_IDX/$N_VMS : $VM_NAME | servidor $COMPUTE_IP | VNC $VNC_PORT"
    read -r VM_VCPUS VM_RAM_MB VM_DISK_GB <<< "$(vm_spec_for_index "$i")"
    read -r VM_IMAGE_NAME VM_IMAGE_URL VM_IMAGE_DOWNLOAD_METHOD VM_CLOUD_INIT <<< "$(image_spec_for_index "$i")"
    VM_KEYPAIR_NAME="$(keypair_spec_for_index "$i")"
    VM_PUBLIC_KEY_B64="$(public_key_b64_for_keypair "$VM_KEYPAIR_NAME")"
    echo "    Flavor efectivo: ${VM_VCPUS} vCPU | ${VM_RAM_MB} MB RAM | ${VM_DISK_GB} GB disco"
    echo "    Imagen: ${VM_IMAGE_NAME} (${VM_IMAGE_URL})"
    echo "    Cloud-init: ${VM_CLOUD_INIT}"
    echo "    Par de llaves: ${VM_KEYPAIR_NAME}"

    VM_VLANS=()
    [ -n "$RIGHT_VLAN" ] && VM_VLANS+=("$RIGHT_VLAN")
    [ -n "$LEFT_VLAN" ] && VM_VLANS+=("$LEFT_VLAN")

    ssh ${SSH_OPTS} ${SSH_USER}@${COMPUTE_IP} \
        "NIMBUSCORE_OVS_UPLINKS='$OVS_UPLINKS' NIMBUSCORE_VM_VCPUS=$VM_VCPUS NIMBUSCORE_VM_RAM_MB=$VM_RAM_MB NIMBUSCORE_VM_DISK_GB=$VM_DISK_GB NIMBUSCORE_BASE_IMAGE_NAME='$VM_IMAGE_NAME' NIMBUSCORE_BASE_IMAGE_URL='$VM_IMAGE_URL' NIMBUSCORE_BASE_IMAGE_DOWNLOAD_METHOD='$VM_IMAGE_DOWNLOAD_METHOD' NIMBUSCORE_ENABLE_CLOUD_INIT='$VM_CLOUD_INIT' NIMBUSCORE_CONSOLE_USER='$CONSOLE_USER' NIMBUSCORE_CONSOLE_PASSWORD='$CONSOLE_PASSWORD' NIMBUSCORE_ENABLE_PASSWORD_LOGIN='$ENABLE_PASSWORD_LOGIN' NIMBUSCORE_KEYPAIR_NAME='$VM_KEYPAIR_NAME' NIMBUSCORE_PUBLIC_KEY_B64='$VM_PUBLIC_KEY_B64' bash ${SCRIPTS_DIR}/create_vm.sh $VM_NAME $OVS_NAME $VNC_PORT ${VM_VLANS[*]}"

    if [ $? -ne 0 ]; then
        echo "  [ERROR] Fallo al crear VM $VM_NAME en $COMPUTE_IP. Abortando."
        exit 1
    fi
done

# ──────────────────────────────────────────────
# PASO 3: Ruteo automático entre VLANs
# Desactivado por defecto para que el tráfico no salte la topología lógica.
# Si se requiere modo demo centralizado, usar NIMBUSCORE_ENABLE_AUTO_ROUTING=true.
# ──────────────────────────────────────────────
echo ""
if [ "$ENABLE_AUTO_ROUTING" = "true" ]; then
    echo "[PASO 3] Habilitando ruteo entre VLANs adyacentes en headnode..."

    for (( link=0; link<N_LINKS-1; link++ )); do
        VLAN_A=$(( VLAN_BASE + link ))
        VLAN_B=$(( VLAN_BASE + link + 1 ))
        echo "  → Ruteo VLAN $VLAN_A ↔ VLAN $VLAN_B"
        run_headnode_script routing_networks.sh "$VLAN_A" "$VLAN_B"
    done
else
    echo "[PASO 3] Ruteo automatico entre VLANs desactivado."
    echo "  Para modo demo centralizado: NIMBUSCORE_ENABLE_AUTO_ROUTING=true"
fi

# ──────────────────────────────────────────────
# Resumen final
# ──────────────────────────────────────────────
echo ""
echo "======================================================"
echo " Topología LINEAL '$SLICE_NAME' creada exitosamente."
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
echo " VLANs (enlaces):"
for (( link=0; link<N_LINKS; link++ )); do
    V=$(( VLAN_BASE + link ))
    C=$(( CIDR_BASE + link ))
    echo "   vm$((link+1)) ── VLAN $V (192.168.$C.0/24) ── vm$((link+2))"
done
echo "======================================================"
