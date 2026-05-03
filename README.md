# NimbusCore

NimbusCore es una aplicacion de gestion de slices que, en esta fase, crea topologias usando scripts sobre un cluster Linux con QEMU/KVM y Open vSwitch. El proyecto esta enfocado en el flujo **Frontend -> API Gateway -> Backend -> Worker -> Script Runner**.

## Componentes

```text
FRONT/
  Interfaz web estatica.

API_Gateway/
  Nginx. Sirve el frontend y reenvia /api/ hacia el backend.

BACKEND/
  API FastAPI. Guarda slices, crea jobs y expone endpoints de despliegue.

WORKER/
  Lee jobs desde /jobs y ejecuta comandos SSH hacia el head node.

SCRIPT_RUNNER/
  Scripts Bash para crear VLANs, DHCP, TAPs, VMs QEMU y reglas de forwarding.
```

## Entorno Probado

La prueba actual usa cuatro servidores:

```text
Cliente/local
  Maquina desde donde se abre el navegador y RealVNC Viewer.

server4 = app/control server
  Acceso externo usado en pruebas: ubuntu@10.20.12.227 -p 5804
  Ejecuta Docker Compose de NimbusCore:
    - API Gateway
    - Frontend
    - Backend
    - Worker
  Debe tener SSH sin password hacia server3.

server3 = head node de red
  IP interna: 10.0.10.3
  Ejecuta scripts de topologia.
  Crea VLANs, interfaces internas OVS, namespaces DHCP y forwarding.
  Debe tener SSH sin password hacia server1.

server1 = worker activo
  IP interna: 10.0.10.1
  Acceso externo usado para VNC/SSH en pruebas: ubuntu@10.20.12.227 -p 5801
  Ejecuta QEMU/KVM.
  Crea VMs, TAPs y puertos OVS tagged.

server2 = worker futuro
  Aun no se usa en la prueba actual.
```

Flujo completo:

```text
Navegador
  -> tunel SSH al server4
  -> API Gateway :8080
  -> Backend
  -> job JSON en volumen /jobs
  -> Worker en server4
  -> SSH a server3
  -> SCRIPT_RUNNER/create_linear_topology.sh o create_ring_topology.sh
  -> server3 crea redes del slice
  -> server3 hace SSH a server1
  -> server1 crea VMs QEMU y TAPs en OVS
```

`server4` no deberia necesitar SSH directo a `server1`. El camino operativo correcto es:

```text
server4 -> server3 -> server1
```

## Túneles Para Usar La App

El puerto recomendado es `8080`, porque ahi esta el API Gateway y por tanto funciona frontend + `/api`.

Desde tu maquina local:

```bash
ssh -N -L 8080:localhost:8080 -p 5804 ubuntu@10.20.12.227
```

Luego abre:

```text
http://localhost:8080/login.html
```

Tambien se puede tunelar `8081`, pero es solo el frontend directo. Sirve para inspeccionar la UI, pero no es el camino recomendado porque no enruta `/api`:

```bash
ssh -N -L 8081:localhost:8081 -p 5804 ubuntu@10.20.12.227
```

## Túneles Para VNC De Las VMs

Las VMs se exponen por VNC en `server1`. Para una topologia de 3 VMs se usan:

```text
vm1 -> 5901
vm2 -> 5902
vm3 -> 5903
```

Tunnel usado en la prueba:

```bash
ssh -NL 5901:127.0.0.1:5901 -NL 5902:localhost:5902 -NL 5903:localhost:5903 ubuntu@10.20.12.227 -p 5801
```

Luego abre RealVNC Viewer en:

```text
127.0.0.1:5901
127.0.0.1:5902
127.0.0.1:5903
```

Usuario de Cirros:

```text
usuario: cirros
password: gocubsgo
```

## Docker En server4

Instalacion usada para Docker:

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg lsb-release git

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Comandos de ejecucion del proyecto en `server4`:

```bash
sudo docker compose build --no-cache
sudo docker compose up -d
sudo docker compose logs -f worker
```

Para detener:

```bash
sudo docker compose down
```

## SSH Y Permisos

El worker corre dentro de Docker en `server4`, pero necesita usar una llave SSH para entrar a `server3`.

Llave usada en la prueba:

```bash
ssh-keygen -t ecdsa -b 256 -C "a20212529@pucp.edu.pe"
```

Copiar llave hacia un servidor:

```bash
ssh-copy-id -i ~/.ssh/id_ecdsa.pub ubuntu@10.0.10.1
```

Para el flujo actual se requiere:

```text
server4 -> server3 sin password
server3 -> server1 sin password
```

Si usas `sudo docker compose`, cuida que el contenedor monte las llaves de `ubuntu`, no las de `root`. En `docker-compose.yml` se usa:

```yaml
${NIMBUSCORE_SSH_DIR:-/home/ubuntu/.ssh}:/root/.ssh:ro
```

Validacion desde `server4`:

```bash
sudo docker compose exec worker ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ubuntu@10.0.10.3 hostname
```

Validacion desde `server3`:

```bash
ssh ubuntu@10.0.10.1 hostname
```

Si cualquiera de esos comandos pide password, el despliegue automatico no va a funcionar.

Para permitir sudo sin password durante la prueba:

```bash
sudo visudo
```

Agregar:

```text
ubuntu ALL=(ALL) NOPASSWD:ALL
```

En un entorno mas estricto, conviene limitar ese permiso solo a `ovs-vsctl`, `ip`, `iptables`, `dnsmasq`, `qemu-system-x86_64` y `qemu-img`.

## Preparar server3

`server3` es el head node. Necesita OVS, dnsmasq e iptables:

```bash
sudo apt update
sudo apt install -y openvswitch-switch dnsmasq iptables
sudo systemctl enable --now openvswitch-switch
sudo ovs-vsctl --may-exist add-br br-int
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-nimbuscore.conf
sudo sysctl --system
```

No agregues `ens3` a OVS si es la interfaz de gestion. Mover la interfaz de gestion al bridge puede cortar la conexion SSH.

## Preparar server1

`server1` es el worker de computo activo. Necesita QEMU/KVM y OVS:

```bash
sudo apt update
sudo apt install -y qemu-kvm qemu-utils openvswitch-switch dnsmasq curl wget
sudo systemctl enable --now openvswitch-switch
sudo ovs-vsctl --may-exist add-br br-int
sudo usermod -aG kvm $USER
```

Validaciones:

```bash
groups
sudo ovs-vsctl show
ls -l /dev/kvm
```

## Distribuir Script Runner

Cada vez que se modifiquen scripts locales, hay que copiarlos a `server3` y `server1`.

Comandos usados en la prueba:

```bash
ssh ubuntu@10.0.10.3 "rm -rf /home/ubuntu/script_runner"
scp -r SCRIPT_RUNNER ubuntu@10.0.10.3:/home/ubuntu/script_runner

ssh ubuntu@10.0.10.1 "rm -rf /home/ubuntu/script_runner"
scp -r SCRIPT_RUNNER ubuntu@10.0.10.1:/home/ubuntu/script_runner

ssh ubuntu@10.0.10.3 "chmod +x /home/ubuntu/script_runner/*.sh"
ssh ubuntu@10.0.10.1 "chmod +x /home/ubuntu/script_runner/*.sh"
```

Los scripts de topologia se ejecutan en `server3`. Por defecto usan:

```bash
NIMBUSCORE_HEADNODE_LOCAL=true
```

Eso significa:

```text
server3:
  crea VLANs, DHCP y forwarding localmente

server1:
  recibe create_vm.sh por SSH desde server3
  crea las VMs y sus TAPs
```

## Variables Del Worker

Variables principales en `docker-compose.yml`:

```yaml
NIMBUSCORE_SCRIPT_DRY_RUN: "false"
NIMBUSCORE_HEADNODE_IP: 10.0.10.3
NIMBUSCORE_COMPUTE_IPS: 10.0.10.1
NIMBUSCORE_REMOTE_SCRIPTS_DIR: /home/ubuntu/script_runner
NIMBUSCORE_SSH_OPTS: "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
NIMBUSCORE_VLAN_BASE: 100
NIMBUSCORE_VNC_BASE: 5901
NIMBUSCORE_CIDR_BASE: 10
```

`NIMBUSCORE_SCRIPT_DRY_RUN`:

```text
false:
  ejecuta realmente SSH y scripts.

true:
  solo imprime el comando preparado y marca el job como procesado.
  no crea VLANs ni VMs.
```

Para usar dos workers despues:

```yaml
NIMBUSCORE_COMPUTE_IPS: 10.0.10.1,10.0.10.2
```

La asignacion de VMs a workers es round-robin. En la prueba actual solo se usa `10.0.10.1`, por eso todas las VMs se crean en `server1`.

## Flujo De Creacion Desde El Frontend

En `configurar-slice.html`, el boton de guardar crea el slice y luego llama al despliegue:

```text
Guardar cambios
  -> POST /api/slices
  -> POST /api/slices/{slice_id}/deploy
```

El backend genera un job JSON en el volumen compartido `/jobs`. El worker detecta ese archivo y ejecuta el comando correspondiente.

Ejemplo de comando generado:

```bash
ssh ubuntu@10.0.10.3 \
  NIMBUSCORE_HEADNODE_LOCAL=true \
  NIMBUSCORE_REMOTE_SCRIPTS_DIR='/home/ubuntu/script_runner' \
  NIMBUSCORE_COMPUTE_IPS='10.0.10.1' \
  bash '/home/ubuntu/script_runner/create_linear_topology.sh' \
  'slice-de-red' '3' '100' '5901' '10'
```

## Scripts Y Parametros

### create_linear_topology.sh

```bash
./create_linear_topology.sh <SLICE_NAME> <N_VMS> <VLAN_BASE> <VNC_BASE> <CIDR_BASE>
```

Ejemplo:

```bash
./create_linear_topology.sh slice1 3 100 5901 10
```

Para `N_VMS=3` crea:

```text
VMs:
  slice1-vm1 -> VNC 5901
  slice1-vm2 -> VNC 5902
  slice1-vm3 -> VNC 5903

VLANs:
  VLAN 100 -> enlace vm1-vm2 -> 192.168.10.0/24
  VLAN 101 -> enlace vm2-vm3 -> 192.168.11.0/24
```

### create_ring_topology.sh

```bash
./create_ring_topology.sh <SLICE_NAME> <N_VMS> <VLAN_BASE> <VNC_BASE> <CIDR_BASE>
```

Para `N_VMS=3` crea tres enlaces:

```text
vm1-vm2
vm2-vm3
vm3-vm1
```

Cada enlace usa una VLAN distinta.

### create_network_vlan.sh

```bash
./create_network_vlan.sh <VLAN_ID> <CIDR> <dhcp|nodhcp> [DHCP_RANGE_START] [DHCP_RANGE_END]
```

Ejemplo:

```bash
./create_network_vlan.sh 100 192.168.10.0/24 dhcp 192.168.10.10 192.168.10.100
```

En `server3` crea:

```text
br-int
  vlan100 internal tag=100
  veth DHCP tag=100

namespace:
  dhcp-ns-vlan100

gateway:
  192.168.10.1

dnsmasq:
  entrega IPs 192.168.10.10 - 192.168.10.100
```

### routing_networks.sh

```bash
./routing_networks.sh <VLAN_A> <VLAN_B>
```

Habilita forwarding entre dos VLANs en `server3` con iptables. Esto permite que las redes creadas se comuniquen pasando por el head node.

### create_vm.sh

```bash
./create_vm.sh <VM_NAME> <OVS_NAME> <VNC_PORT> <VLAN_1> [VLAN_2 ...]
```

Ejemplo:

```bash
./create_vm.sh slice1-vm2 br-int 5902 100 101
```

En `server1` hace:

```text
1. Crea o reutiliza /var/lib/qemu/images/cirros-base.img.
2. Crea un disco overlay qcow2 por VM.
3. Crea una interfaz TAP por VLAN.
4. Agrega cada TAP a br-int con tag VLAN.
5. Arranca qemu-system-x86_64 con VNC.
```

Estructura resultante:

```text
VM QEMU
  NIC virtio
    |
  TAP Linux
    |
  puerto OVS tagged
    |
  br-int
```

## Red De Las Topologias

La implementacion actual usa **una VLAN por enlace logico**, no una VLAN unica para todo el slice.

Topologia lineal de 3 VMs:

```text
vm1 --- VLAN 100 --- vm2 --- VLAN 101 --- vm3
```

Resultado:

```text
vm1:
  eth0 -> VLAN 100

vm2:
  eth0 -> VLAN 100
  eth1 -> VLAN 101

vm3:
  eth0 -> VLAN 101
```

Por eso la VM intermedia puede hacer ping a ambos lados. Eso es esperado.

Importante: actualmente `server3` tambien puede enrutar entre VLANs usando `routing_networks.sh`. Entonces el trafico entre extremos puede pasar por el head node:

```text
vm1 -> server3 -> vm3
```

Si se requiere una topologia estricta donde el trafico pase por la VM intermedia, habria que cambiar el modelo:

```text
server3:
  solo DHCP/red por enlace
  sin forwarding entre VLANs

vm intermedia:
  ip_forward=1
  rutas/reglas para reenviar trafico
```

## Transporte Entre OVS

Los scripts crean TAPs y VLANs en los bridges OVS, pero asumen que existe conectividad L2 para transportar esas VLANs entre `server3` y `server1`.

Si `br-int` de `server3` y `br-int` de `server1` no comparten un trunk, VXLAN, GRE o una configuracion equivalente, las VMs pueden crearse pero no necesariamente recibiran DHCP ni tendran conectividad con las redes del head node.

Validaciones utiles:

```bash
# server1
sudo ovs-vsctl show
sudo ovs-vsctl list port | grep -E "name|tag|external_ids"
ps aux | grep qemu

# server3
sudo ovs-vsctl show
ip a | grep vlan
sudo ip netns list
sudo iptables -L FORWARD -n -v
```

## Parametros Del Frontend Que Se Usan

Actualmente se usa principalmente:

```text
nombre:
  nombre base del slice.

topologias[].type:
  lineal o anillo.

topologias[].count:
  cantidad de VMs para esa topologia.
```

El backend transforma esos datos en:

```text
SLICE_NAME
N_VMS
VLAN_BASE
VNC_BASE
CIDR_BASE
```

`VLAN_BASE`, `VNC_BASE` y `CIDR_BASE` no vienen del frontend. Se asignan automaticamente desde variables del worker:

```text
NIMBUSCORE_VLAN_BASE=100
NIMBUSCORE_VNC_BASE=5901
NIMBUSCORE_CIDR_BASE=10
```

## Parametros Del Frontend Todavia No Usados

Estos campos pueden existir o aparecer en la UI, pero todavia no controlan la VM real:

```text
zona de disponibilidad:
  no decide worker todavia.
  el worker usado sale de NIMBUSCORE_COMPUTE_IPS.

flavor:
  no se traduce todavia a CPU/RAM por VM desde el front.
  create_vm.sh usa NIMBUSCORE_VM_RAM_MB y NIMBUSCORE_VM_VCPUS.

disco:
  no se usa desde el front.
  create_vm.sh crea overlays qcow2 sobre la imagen base.

imagen:
  no se selecciona desde el front.
  por defecto usa Cirros:
  https://download.cirros-cloud.net/0.6.2/cirros-0.6.2-x86_64-disk.img

par de llaves:
  no se inyecta en la VM todavia.

reglas de seguridad:
  no se aplican por VM todavia.

nodos y enlaces manuales:
  se guardan como parte del slice, pero el worker actual despliega segun topologias[].type y topologias[].count.
```

Variables soportadas por `create_vm.sh`:

```text
NIMBUSCORE_BASE_IMAGE_URL
NIMBUSCORE_QEMU_IMAGE_DIR
NIMBUSCORE_BASE_IMAGE_PATH
NIMBUSCORE_VM_RAM_MB
NIMBUSCORE_VM_VCPUS
```

## Topologias Soportadas

Implementadas:

```text
lineal
anillo
```

Pendientes:

```text
arbol
bus
topologias manuales desde nodos/enlaces
```

Si se selecciona una topologia no soportada por el worker, el job falla con error de topologia no soportada.

## Endpoints Principales

```text
GET    /api/health
POST   /api/auth/login
GET    /api/cursos
GET    /api/slices
POST   /api/slices
GET    /api/slices/{slice_id}
PUT    /api/slices/{slice_id}
DELETE /api/slices/{slice_id}
POST   /api/slices/{slice_id}/deploy
GET    /api/deployments/{job_id}
POST   /api/slices/{slice_id}/start
POST   /api/slices/{slice_id}/stop
POST   /api/slices/{slice_id}/restart
POST   /api/slices/{slice_id}/destroy
```
