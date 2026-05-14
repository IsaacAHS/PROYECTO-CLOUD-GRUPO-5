#!/bin/bash
set -euo pipefail

# Crea una topologia personalizada.
#
# Uso:
#   ./create_custom_topology.sh <SLICE_NAME> <N_VMS> <VLAN_BASE> <VNC_BASE> <CIDR_BASE>
#
# Enlaces:
#   NIMBUSCORE_TOPOLOGY_LINK_SPECS="0-1;1-2;1-3"
#
# Cada enlace usa una VLAN dedicada. Los indices de enlace son 0-based y se
# refieren al orden de nodos de la topologia: vm1=0, vm2=1, etc.

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
ENABLE_AUTO_ROUTING="${NIMBUSCORE_ENABLE_AUTO_ROUTING:-false}"
DEFAULT_VM_SPEC="${NIMBUSCORE_DEFAULT_VM_SPEC:-1:2048:1}"
LINK_SPECS_RAW="${NIMBUSCORE_TOPOLOGY_LINK_SPECS:-}"
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

SLICE_NAME="${1:-}"
N_VMS="${2:-}"
VLAN_BASE="${3:-}"
VNC_BASE="${4:-}"
CIDR_BASE="${5:-}"

if [ -z "$SLICE_NAME" ] || [ -z "$N_VMS" ] || [ -z "$VLAN_BASE" ] || \
   [ -z "$VNC_BASE" ] || [ -z "$CIDR_BASE" ]; then
    echo "Uso: $0 <SLICE_NAME> <N_VMS> <VLAN_BASE> <VNC_BASE> <CIDR_BASE>"
    exit 1
fi

if [ "$N_VMS" -lt 1 ]; then
    echo "Error: Se necesita al menos 1 VM para una topologia personalizada."
    exit 1
fi

N_COMPUTE=${#COMPUTE_IPS[@]}
if [ "$N_COMPUTE" -lt 1 ]; then
    echo "Error: NIMBUSCORE_COMPUTE_IPS no contiene servidores de computo."
    exit 1
fi

LINK_A=()
LINK_B=()

if [ -n "$LINK_SPECS_RAW" ]; then
    IFS=';' read -r -a LINK_SPECS <<< "$LINK_SPECS_RAW"
    for spec in "${LINK_SPECS[@]}"; do
        [ -z "$spec" ] && continue
        IFS='-' read -r a b <<< "$spec"
        if ! [[ "$a" =~ ^[0-9]+$ && "$b" =~ ^[0-9]+$ ]]; then
            echo "Error: enlace personalizado invalido: $spec"
            exit 1
        fi
        if [ "$a" -eq "$b" ] || [ "$a" -ge "$N_VMS" ] || [ "$b" -ge "$N_VMS" ]; then
            echo "Error: enlace fuera de rango: $spec"
            exit 1
        fi
        LINK_A+=("$a")
        LINK_B+=("$b")
    done
fi

N_LINKS=${#LINK_A[@]}

echo "======================================================"
echo " Creando topologia PERSONALIZADA: $SLICE_NAME"
echo " VMs      : $N_VMS"
echo " Enlaces  : $N_LINKS"
if [ "$N_LINKS" -gt 0 ]; then
    echo " VLANs    : $VLAN_BASE -> $(( VLAN_BASE + N_LINKS - 1 ))"
else
    echo " VLANs    : sin enlaces"
fi
echo " VNC      : $VNC_BASE -> $(( VNC_BASE + N_VMS - 1 ))"
echo "======================================================"

if [ "$N_LINKS" -gt 0 ]; then
    echo ""
    echo "[PASO 1] Creando redes VLAN en el headnode ($HEADNODE_IP)..."

    for (( link=0; link<N_LINKS; link++ )); do
        VLAN_ID=$(( VLAN_BASE + link ))
        CIDR_THIRD=$(( CIDR_BASE + link ))
        CIDR="192.168.${CIDR_THIRD}.0/24"
        DHCP_START="192.168.${CIDR_THIRD}.10"
        DHCP_END="192.168.${CIDR_THIRD}.100"
        VM_A=$(( LINK_A[$link] + 1 ))
        VM_B=$(( LINK_B[$link] + 1 ))

        echo "  -> Enlace vm${VM_A}<->vm${VM_B} : VLAN $VLAN_ID | Red $CIDR"
        run_headnode_script create_network_vlan.sh "$VLAN_ID" "$CIDR" dhcp "$DHCP_START" "$DHCP_END"
    done
else
    echo ""
    echo "[PASO 1] Sin enlaces: se crearan VMs aisladas sin NICs."
fi

echo ""
echo "[PASO 2] Creando VMs en servidores de computo..."

for (( i=0; i<N_VMS; i++ )); do
    VM_IDX=$(( i + 1 ))
    VM_NAME="${SLICE_NAME}-vm${VM_IDX}"
    VNC_PORT=$(( VNC_BASE + i ))
    COMPUTE_IP="${COMPUTE_IPS[$((i % N_COMPUTE))]}"
    VM_VLANS=()

    for (( link=0; link<N_LINKS; link++ )); do
        if [ "${LINK_A[$link]}" -eq "$i" ] || [ "${LINK_B[$link]}" -eq "$i" ]; then
            VM_VLANS+=("$(( VLAN_BASE + link ))")
        fi
    done

    echo "  -> VM $VM_IDX/$N_VMS : $VM_NAME | servidor $COMPUTE_IP | VNC $VNC_PORT | VLANs: ${VM_VLANS[*]:-sin-enlaces}"
    read -r VM_VCPUS VM_RAM_MB VM_DISK_GB <<< "$(vm_spec_for_index "$i")"
    read -r VM_IMAGE_NAME VM_IMAGE_URL VM_IMAGE_DOWNLOAD_METHOD VM_CLOUD_INIT <<< "$(image_spec_for_index "$i")"
    VM_KEYPAIR_NAME="$(keypair_spec_for_index "$i")"
    VM_PUBLIC_KEY_B64="$(public_key_b64_for_keypair "$VM_KEYPAIR_NAME")"
    echo "    Flavor efectivo: ${VM_VCPUS} vCPU | ${VM_RAM_MB} MB RAM | ${VM_DISK_GB} GB disco"
    echo "    Imagen: ${VM_IMAGE_NAME} (${VM_IMAGE_URL})"
    echo "    Cloud-init: ${VM_CLOUD_INIT}"
    echo "    Par de llaves: ${VM_KEYPAIR_NAME}"

    ssh ${SSH_OPTS} ${SSH_USER}@${COMPUTE_IP} \
        "NIMBUSCORE_OVS_UPLINKS='$OVS_UPLINKS' NIMBUSCORE_VM_VCPUS=$VM_VCPUS NIMBUSCORE_VM_RAM_MB=$VM_RAM_MB NIMBUSCORE_VM_DISK_GB=$VM_DISK_GB NIMBUSCORE_BASE_IMAGE_NAME='$VM_IMAGE_NAME' NIMBUSCORE_BASE_IMAGE_URL='$VM_IMAGE_URL' NIMBUSCORE_BASE_IMAGE_DOWNLOAD_METHOD='$VM_IMAGE_DOWNLOAD_METHOD' NIMBUSCORE_ENABLE_CLOUD_INIT='$VM_CLOUD_INIT' NIMBUSCORE_CONSOLE_USER='$CONSOLE_USER' NIMBUSCORE_CONSOLE_PASSWORD='$CONSOLE_PASSWORD' NIMBUSCORE_ENABLE_PASSWORD_LOGIN='$ENABLE_PASSWORD_LOGIN' NIMBUSCORE_KEYPAIR_NAME='$VM_KEYPAIR_NAME' NIMBUSCORE_PUBLIC_KEY_B64='$VM_PUBLIC_KEY_B64' bash ${SCRIPTS_DIR}/create_vm.sh $VM_NAME $OVS_NAME $VNC_PORT ${VM_VLANS[*]}"
done

if [ "$N_LINKS" -gt 1 ]; then
    echo ""
    if [ "$ENABLE_AUTO_ROUTING" = "true" ]; then
        echo "[PASO 3] Habilitando ruteo entre VLANs que comparten VM..."

        for (( i=0; i<N_VMS; i++ )); do
            VM_VLANS=()
            for (( link=0; link<N_LINKS; link++ )); do
                if [ "${LINK_A[$link]}" -eq "$i" ] || [ "${LINK_B[$link]}" -eq "$i" ]; then
                    VM_VLANS+=("$(( VLAN_BASE + link ))")
                fi
            done

            for (( a=0; a<${#VM_VLANS[@]}; a++ )); do
                for (( b=a+1; b<${#VM_VLANS[@]}; b++ )); do
                    echo "  -> Ruteo VLAN ${VM_VLANS[$a]} <-> ${VM_VLANS[$b]} por vm$(( i + 1 ))"
                    run_headnode_script routing_networks.sh "${VM_VLANS[$a]}" "${VM_VLANS[$b]}"
                done
            done
        done
    else
        echo "[PASO 3] Ruteo automatico entre VLANs desactivado."
        echo "  Para modo demo centralizado: NIMBUSCORE_ENABLE_AUTO_ROUTING=true"
    fi
fi

echo ""
echo "======================================================"
echo " Topologia PERSONALIZADA '$SLICE_NAME' creada exitosamente."
echo ""
echo " Nodos y acceso VNC:"
for (( i=0; i<N_VMS; i++ )); do
    VM_IDX=$(( i + 1 ))
    VM_NAME="${SLICE_NAME}-vm${VM_IDX}"
    VNC_PORT=$(( VNC_BASE + i ))
    COMPUTE_IP="${COMPUTE_IPS[$((i % N_COMPUTE))]}"
    echo "   $VM_NAME  ->  $COMPUTE_IP  |  VNC: $COMPUTE_IP:$VNC_PORT"
done

if [ "$N_LINKS" -gt 0 ]; then
    echo ""
    echo " Enlaces/VLANs:"
    for (( link=0; link<N_LINKS; link++ )); do
        VM_A=$(( LINK_A[$link] + 1 ))
        VM_B=$(( LINK_B[$link] + 1 ))
        VLAN_ID=$(( VLAN_BASE + link ))
        CIDR_THIRD=$(( CIDR_BASE + link ))
        echo "   vm${VM_A} -- VLAN $VLAN_ID (192.168.$CIDR_THIRD.0/24) -- vm${VM_B}"
    done
fi
echo "======================================================"
