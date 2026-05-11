# NimbusCore

NimbusCore es una aplicacion de gestion de slices que crea topologias sobre un cluster Linux usando **Script Runner**, **QEMU/KVM** y **Open vSwitch**. En el enfoque actual, `server4` concentra la aplicacion y tambien actua como head node de red.

## Componentes

```text
FRONT/
  Interfaz web estatica.

API_Gateway/
  Nginx. Sirve el frontend y reenvia /api/ hacia el backend.

BACKEND/
  API FastAPI. Guarda plantillas, cursos, slices asignados, jobs y endpoints de despliegue.

WORKER/
  Lee jobs desde /jobs y ejecuta comandos SSH hacia el head node.

SCRIPT_RUNNER/
  Scripts Bash para crear VLANs, DHCP, TAPs, VMs QEMU y reglas de forwarding.
```

## Arquitectura Actual

La prueba actual usa `server4` como **app/control server + head node**:

```text
Cliente/local
  Maquina desde donde se abre el navegador y RealVNC Viewer.

server4 = app/control server + head node
  IP interna asumida: 10.0.10.4
  Acceso externo usado en pruebas: ubuntu@10.20.12.227 -p 5804
  Ejecuta Docker Compose de NimbusCore:
    - API Gateway
    - Frontend
    - Backend
    - Worker
  Ejecuta Script Runner como head node:
    - Open vSwitch
    - VLANs del slice
    - namespaces DHCP
    - forwarding/routing entre VLANs
  Debe tener SSH sin password hacia si mismo usando 10.0.10.4.
  Debe tener SSH sin password hacia server1, server2 y server3.

server1 = worker de computo
  IP interna: 10.0.10.1
  Acceso externo usado en pruebas: ubuntu@10.20.12.227 -p 5801
  Ejecuta QEMU/KVM, OVS, TAPs y VMs.

server2 = worker de computo
  IP interna: 10.0.10.2
  Ejecuta QEMU/KVM, OVS, TAPs y VMs.

server3 = worker de computo
  IP interna: 10.0.10.3
  Ejecuta QEMU/KVM, OVS, TAPs y VMs.
```

Si la IP interna real de `server4` no es `10.0.10.4`, cambia `NIMBUSCORE_HEADNODE_IP` en `docker-compose.yml` o exportala antes de levantar Docker Compose.

Flujo completo:

```text
Navegador
  -> tunel SSH al server4
  -> API Gateway :8080
  -> Backend
  -> job JSON en volumen /jobs
  -> Worker en Docker dentro de server4
  -> SSH a ubuntu@10.0.10.4
  -> SCRIPT_RUNNER/create_linear_topology.sh o create_ring_topology.sh en server4
  -> server4 crea VLANs, DHCP y forwarding localmente
  -> server4 hace SSH a server1/server2/server3
  -> workers crean VMs QEMU y TAPs en OVS
```

El contenedor `worker` no ejecuta OVS directamente dentro del contenedor. Hace SSH al host `server4`, y el host ejecuta los scripts con `sudo`.

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

Las VMs se exponen por VNC en el worker donde fueron creadas. Con la configuracion actual:

```yaml
NIMBUSCORE_COMPUTE_IPS: 10.0.10.1,10.0.10.2,10.0.10.3
NIMBUSCORE_OVS_UPLINKS: ens4
NIMBUSCORE_VNC_BASE: 5901
NIMBUSCORE_VNC_BLOCK_SIZE: 100
NIMBUSCORE_VLAN_BASE: 100
NIMBUSCORE_VLAN_BLOCK_SIZE: 100
NIMBUSCORE_CIDR_BASE: 10
NIMBUSCORE_CIDR_BLOCK_SIZE: 20
```

El worker reserva un bloque de puertos VNC por slice en `SCRIPT_RUNS/vnc_allocations.json`. Asi evita que varios slices empiecen todos en `5901`.

Tambien reserva un bloque de VLANs y subredes por slice en `SCRIPT_RUNS/network_allocations.json`. Asi evita que varios slices usen todos `VLAN 100` y `192.168.10.0/24`.

`NIMBUSCORE_OVS_UPLINKS` indica que interfaz fisica se usa como transporte L2 entre el `br-int` de `server4` y el `br-int` de los workers. En las pruebas se usa `ens4`. No uses `ens3` si es la interfaz de gestion/SSH.

Ejemplo:

```text
slice 1 -> bloque 5901-6000
slice 2 -> bloque 6001-6100
slice 3 -> bloque 6101-6200
```

Dentro de un mismo slice, si hay varias topologias, el `vnc_cursor` va avanzando por cantidad de VMs. Por ejemplo, una topologia de 3 VMs usa `5901-5903`; si en el mismo slice hay otra topologia de 2 VMs, usa `5904-5905`.

Para el primer slice, con una topologia de 3 VMs y asignacion round-robin:

```text
vm1 -> server1 -> 10.0.10.1:5901
vm2 -> server2 -> 10.0.10.2:5902
vm3 -> server3 -> 10.0.10.3:5903
```

Si tu laboratorio mantiene puertos externos `5801`, `5802`, `5803` para `server1`, `server2`, `server3`, puedes abrir tres tuneles separados:

```bash
ssh -NL 5901:127.0.0.1:5901 ubuntu@10.20.12.227 -p 5801
ssh -NL 5902:127.0.0.1:5902 ubuntu@10.20.12.227 -p 5802
ssh -NL 5903:127.0.0.1:5903 ubuntu@10.20.12.227 -p 5803
```

Si todas las VMs estan temporalmente en `server1`, este tunel tambien sirve:

```bash
ssh -NL 5901:127.0.0.1:5901 -NL 5902:localhost:5902 -NL 5903:localhost:5903 ubuntu@10.20.12.227 -p 5801
```

Luego abre RealVNC Viewer en:

```text
127.0.0.1:5901
127.0.0.1:5902
127.0.0.1:5903
```

Credenciales por VNC/consola:

```text
Ubuntu/Debian cloud images:
  usuario: nimbus
  password: NimbusCore123
  creado por cloud-init al primer arranque.

Cirros:
  usuario: cirros
  password: gocubsgo
```

Si cambias `NIMBUSCORE_CONSOLE_USER` o `NIMBUSCORE_CONSOLE_PASSWORD`, esas credenciales aplican a las VMs nuevas que reciban cloud-init. Las VMs ya creadas no se modifican.

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

## Preparar SSH

El worker corre dentro de Docker en `server4`, pero necesita usar una llave SSH para entrar al **host server4** y para que `server4` entre a los workers.

Llave usada en la prueba:

```bash
ssh-keygen -t ecdsa -b 256 -C "a20212529@pucp.edu.pe"
```

Copiar llave desde `server4` hacia el propio head node por su IP interna:

```bash
ssh-copy-id -i ~/.ssh/id_ecdsa.pub ubuntu@10.0.10.4
```

Copiar llave desde `server4` hacia los workers:

```bash
ssh-copy-id -i ~/.ssh/id_ecdsa.pub ubuntu@10.0.10.1
ssh-copy-id -i ~/.ssh/id_ecdsa.pub ubuntu@10.0.10.2
ssh-copy-id -i ~/.ssh/id_ecdsa.pub ubuntu@10.0.10.3
```

Validar desde el contenedor `worker` que puede entrar al host `server4`:

```bash
sudo docker compose exec worker ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ubuntu@10.0.10.4 hostname
```

Validar desde `server4` que puede entrar a los workers:

```bash
ssh ubuntu@10.0.10.1 hostname
ssh ubuntu@10.0.10.2 hostname
ssh ubuntu@10.0.10.3 hostname
```

Si cualquiera de esos comandos pide password, el despliegue automatico no va a funcionar.

Si usas `sudo docker compose`, cuida que el contenedor monte las llaves de `ubuntu`, no las de `root`. En `docker-compose.yml` se usa:

```yaml
${NIMBUSCORE_SSH_DIR:-/home/ubuntu/.ssh}:/root/.ssh:ro
```

## Sudo Sin Password

Durante la prueba se esta usando sudo sin password:

```bash
sudo visudo
```

Agregar:

```text
ubuntu ALL=(ALL) NOPASSWD:ALL
```

Debe estar configurado en:

```text
server4:
  para crear VLANs, namespaces, dnsmasq, iptables y OVS.

server1/server2/server3:
  para crear TAPs, puertos OVS, discos qcow2 y VMs QEMU.
```

En un entorno mas estricto, conviene limitar ese permiso solo a `ovs-vsctl`, `ip`, `iptables`, `dnsmasq`, `qemu-system-x86_64` y `qemu-img`.

## Preparar server4 Como Head Node

`server4` necesita Docker y tambien herramientas de red para actuar como head node:

```bash
sudo apt update
sudo apt install -y openvswitch-switch dnsmasq iptables
sudo systemctl enable --now openvswitch-switch
sudo ovs-vsctl --may-exist add-br br-int
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-nimbuscore.conf
sudo sysctl --system
```

No agregues `ens3` a OVS si es la interfaz de gestion. Mover la interfaz de gestion al bridge puede cortar la conexion SSH.

## Preparar server1/server2/server3 Como Workers

Cada worker necesita QEMU/KVM y OVS:

```bash
sudo apt update
sudo apt install -y qemu-kvm qemu-utils openvswitch-switch dnsmasq curl wget cloud-image-utils genisoimage
sudo systemctl enable --now openvswitch-switch
sudo ovs-vsctl --may-exist add-br br-int
sudo ip link set br-int up
sudo usermod -aG kvm $USER
```

Los scripts tambien intentan levantar `br-int` automaticamente al crear VLANs o VMs. Aun asi, la preparacion inicial ayuda a detectar errores antes de la demo.

Validaciones:

```bash
groups
sudo ovs-vsctl show
ls -l /dev/kvm
```

## Distribuir Script Runner

Cada vez que se modifiquen scripts locales, hay que copiarlos a `server4` y a todos los workers.

En `server4`, desde la raiz del proyecto:

```bash
rm -rf /home/ubuntu/script_runner
cp -r SCRIPT_RUNNER /home/ubuntu/script_runner
chmod +x /home/ubuntu/script_runner/*.sh
```

En los workers:

```bash
ssh ubuntu@10.0.10.1 "rm -rf /home/ubuntu/script_runner"
scp -r SCRIPT_RUNNER ubuntu@10.0.10.1:/home/ubuntu/script_runner

ssh ubuntu@10.0.10.2 "rm -rf /home/ubuntu/script_runner"
scp -r SCRIPT_RUNNER ubuntu@10.0.10.2:/home/ubuntu/script_runner

ssh ubuntu@10.0.10.3 "rm -rf /home/ubuntu/script_runner"
scp -r SCRIPT_RUNNER ubuntu@10.0.10.3:/home/ubuntu/script_runner

ssh ubuntu@10.0.10.1 "chmod +x /home/ubuntu/script_runner/*.sh"
ssh ubuntu@10.0.10.2 "chmod +x /home/ubuntu/script_runner/*.sh"
ssh ubuntu@10.0.10.3 "chmod +x /home/ubuntu/script_runner/*.sh"
```

## Preparar Pares De Llaves Para VMs

Hay dos llaves distintas en el proyecto:

```text
llave de control:
  permite que server4 controle server4/server1/server2/server3 por SSH.

llaves de VMs:
  son las que el usuario selecciona en el frontend.
  Ahora se listan desde los archivos .pub reales que existen en server4.
```

La llave privada de una VM se queda en el cliente/usuario. NimbusCore solo conserva la llave publica `.pub` para inyectarla en la VM con cloud-init.

Cuando creas una llave desde la interfaz, el archivo `.pem` se descarga en la maquina donde tienes abierto el navegador. Aunque entres a la interfaz por tunel SSH, la descarga queda en tu equipo local. En `server4` solo queda la publica, por ejemplo `/home/ubuntu/nimbuscore-keys/key-dev.pub`; por eso no deberia existir `/home/ubuntu/nimbuscore-keys/key-dev.pem`.

En `server4`, el directorio fuente de verdad es:

```text
/home/ubuntu/nimbuscore-keys
```

`docker-compose.yml` monta ese directorio en el backend como `/keypairs`. Por eso el backend puede listar y crear pares de llaves usando:

```text
GET  /api/keypairs
POST /api/keypairs
```

Si ya tienes llaves creadas manualmente, basta con dejar sus `.pub` en:

```text
/home/ubuntu/nimbuscore-keys/<nombre>.pub
```

Ejemplo:

```bash
mkdir -p /home/ubuntu/nimbuscore-keys
ssh-keygen -t ecdsa -b 256 -f ~/.ssh/key-dev -C "key-dev"
cp ~/.ssh/key-dev.pub /home/ubuntu/nimbuscore-keys/key-dev.pub
```

El frontend ya no muestra `key-dev`, `key-prod`, etc. hardcodeados. Solo muestra lo que devuelve `/api/keypairs`. Si no hay ninguna llave, la UI permite crear una. Al crearla, el backend devuelve la llave privada una sola vez para descargarla y guarda la publica en `/home/ubuntu/nimbuscore-keys`.

No es necesario copiar las llaves publicas a todos los workers. Los scripts de topologia se ejecutan en `server4`, leen la `.pub` desde `/home/ubuntu/nimbuscore-keys`, la pasan al worker codificada en base64 y `create_vm.sh` la usa para crear el ISO cloud-init.

## Preparar Catalogo De Imagenes

Las imagenes disponibles ya no estan hardcodeadas en el frontend. El backend las lee desde:

```text
BACKEND_DATA/images.json
```

En Docker Compose ese archivo se monta como:

```text
/data/images.json
```

El frontend llama:

```text
GET /api/images
```

Y llena el selector de imagenes con las entradas activas del JSON. Cada entrada usa este formato:

```json
{
  "id": "ubuntu-20-drive",
  "name": "focal-server-cloudimg-amd64.img",
  "label": "Ubuntu 20.04 Focal (Google Drive)",
  "url": "https://drive.usercontent.google.com/download?id=169719Mq3URSPKf2y6x-uAJ0vluH31i5n&export=download&confirm=t",
  "download_method": "wget-no-check-certificate",
  "active": true
}
```

Para registrar o actualizar una imagen por API:

```text
POST /api/images
```

Metodos soportados:

```text
auto:
  usa wget normal o curl normal.

wget-no-check-certificate:
  usa wget --no-check-certificate.
  si no hay wget, usa curl -k.
```

La entrada `ubuntu-20-drive` reproduce este metodo manual:

```bash
wget --no-check-certificate \
  "https://drive.usercontent.google.com/download?id=169719Mq3URSPKf2y6x-uAJ0vluH31i5n&export=download&confirm=t" \
  -O focal-server-cloudimg-amd64.img
```

Cuando se crea la VM, el worker genera un ISO cloud-init en:

```text
/var/lib/qemu/cloud-init/<vm>-seed.iso
```

Ese ISO contiene:

```text
llave publica seleccionada:
  se agrega a authorized_keys del usuario por defecto y del usuario de consola.

usuario de consola:
  usuario: nimbus
  password: NimbusCore123
```

El usuario de consola se crea con sudo sin password para facilitar la prueba:

```text
nimbus ALL=(ALL) NOPASSWD:ALL
```

Para entrar luego a la VM, usas la llave privada correspondiente. Desde `server4`, si tuvieras la llave privada ahi, bastaria con:

```bash
ssh -i key-dev.pem nimbus@<IP_DE_LA_VM>
```

Pero en la prueba real la llave privada queda en tu maquina local, asi que el acceso recomendado es usar `server4` como salto:

```bash
chmod 600 key-dev.pem

ssh -i key-dev.pem \
  -o ProxyCommand="ssh -p 5804 -W %h:%p ubuntu@10.20.12.227" \
  -o IPQoS=none \
  -o KexAlgorithms=ecdh-sha2-nistp256 \
  nimbus@<IP_DE_LA_VM>
```

Ejemplo observado:

```bash
ssh -i key-dev.pem \
  -o ProxyCommand="ssh -p 5804 -W %h:%p ubuntu@10.20.12.227" \
  -o IPQoS=none \
  -o KexAlgorithms=ecdh-sha2-nistp256 \
  nimbus@192.168.10.57
```

`IPQoS=none` y `KexAlgorithms=ecdh-sha2-nistp256` se agregaron porque en la prueba el handshake SSH hacia la VM se cerraba durante el intercambio de claves. La llave estaba bien inyectada por cloud-init; el problema estaba en esa negociacion SSH a traves del salto.

Tambien puedes dejarlo en `~/.ssh/config`:

```sshconfig
Host nimbus-vm-*
  User nimbus
  IdentityFile ~/Documentos/CLOUD/PROYECTO/IMAGENES/key-dev.pem
  ProxyCommand ssh -p 5804 -W %h:%p ubuntu@10.20.12.227
  IPQoS none
  KexAlgorithms ecdh-sha2-nistp256
```

Y luego entrar con:

```bash
ssh -o HostName=192.168.10.57 nimbus-vm-demo
```

Por VNC, en Ubuntu/Debian usa:

```text
nimbus / NimbusCore123
```

Para Cirros, el usuario por defecto sigue siendo:

```text
cirros / gocubsgo
```

Los scripts de topologia se ejecutan en `server4`. Por defecto usan:

```bash
NIMBUSCORE_HEADNODE_LOCAL=true
```

Eso significa:

```text
server4:
  crea VLANs, DHCP y forwarding localmente.

server1/server2/server3:
  reciben create_vm.sh por SSH desde server4.
  crean las VMs y sus TAPs.
```

## Variables Del Worker

Variables principales en `docker-compose.yml`:

```yaml
backend:
  NIMBUSCORE_KEYPAIR_DIR: /keypairs
  NIMBUSCORE_IMAGE_CATALOG_PATH: /data/images.json
  NIMBUSCORE_SLICE_STORE_PATH: /data/slices.json
  NIMBUSCORE_SLICE_TEMPLATE_STORE_PATH: /data/slice_templates.json
  NIMBUSCORE_ACADEMIC_STORE_PATH: /data/academic.json
  NIMBUSCORE_DEPLOYMENT_STORE_PATH: /data/deployments.json
  NIMBUSCORE_VM_INVENTORY_PATH: /script-runs/vm_inventory.json

backend volumes:
  /home/ubuntu/nimbuscore-keys:/keypairs
  ./BACKEND_DATA:/data
  ./SCRIPT_RUNS:/script-runs:ro

worker:
NIMBUSCORE_SCRIPT_DRY_RUN: "false"
NIMBUSCORE_VM_INVENTORY_PATH: /script-runs/vm_inventory.json
NIMBUSCORE_HEADNODE_IP: 10.0.10.4
NIMBUSCORE_COMPUTE_IPS: 10.0.10.1,10.0.10.2,10.0.10.3
NIMBUSCORE_OVS_UPLINKS: ens4
NIMBUSCORE_REMOTE_SCRIPTS_DIR: /home/ubuntu/script_runner
NIMBUSCORE_KEYPAIR_DIR: /home/ubuntu/nimbuscore-keys
NIMBUSCORE_SSH_OPTS: "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
NIMBUSCORE_CONSOLE_USER: nimbus
NIMBUSCORE_CONSOLE_PASSWORD: NimbusCore123
NIMBUSCORE_ENABLE_PASSWORD_LOGIN: "true"
NIMBUSCORE_VLAN_BASE: 100
NIMBUSCORE_VLAN_BLOCK_SIZE: 100
NIMBUSCORE_VNC_BASE: 5901
NIMBUSCORE_VNC_BLOCK_SIZE: 100
NIMBUSCORE_CIDR_BASE: 10
NIMBUSCORE_CIDR_BLOCK_SIZE: 20
```

## Persistencia Local

La persistencia simple del proyecto vive en archivos JSON:

```text
BACKEND_DATA/images.json:
  catalogo de imagenes disponibles para el frontend y backend.

BACKEND_DATA/academic.json:
  cursos y alumnos base del sistema.
  por ahora siempre existen 2 cursos, cada uno con 3 alumnos.

BACKEND_DATA/slice_templates.json:
  plantillas de slices creadas desde configurar-slice.html.
  guardar una plantilla no crea VMs ni ejecuta el worker.

BACKEND_DATA/slices.json:
  slices reales de la aplicacion.
  en el flujo academico actual se crean al asignar una plantilla a un curso:
  1 slice por alumno del curso.

BACKEND_DATA/deployments.json:
  historial/estado conocido de deployments.

SCRIPT_RUNS/vnc_allocations.json:
  bloques VNC asignados por slice.

SCRIPT_RUNS/network_allocations.json:
  bloques VLAN/CIDR asignados por slice.

SCRIPT_RUNS/vm_inventory.json:
  inventario real planificado/ejecutado por slice.
  guarda VMs, worker, VNC, VLANs, NICs, MACs, TAPs, imagen, flavor, disco y llave.

volumen Docker nimbuscore_jobs:
  jobs enviados del backend al worker.
```

Los slices ya no dependen solo de memoria. Si reinicias el contenedor `backend`, se vuelven a cargar desde `BACKEND_DATA/slices.json`.

Para empezar una demo desde cero:

```bash
sudo docker compose down -v
rm -rf SCRIPT_RUNS

cat > BACKEND_DATA/slices.json <<'EOF'
{
  "updated_at": "2026-05-10T00:00:00+00:00",
  "slices": {}
}
EOF

cat > BACKEND_DATA/slice_templates.json <<'EOF'
{
  "updated_at": "2026-05-10T00:00:00+00:00",
  "templates": {}
}
EOF

cat > BACKEND_DATA/deployments.json <<'EOF'
{
  "updated_at": "2026-05-10T00:00:00+00:00",
  "deployments": {}
}
EOF

sudo docker compose build --no-cache
sudo docker compose up -d
```

Si hay VMs antiguas corriendo, destruyelas o apagarlas antes de resetear `SCRIPT_RUNS`; si no, los contadores VNC/VLAN/CIDR pueden empezar desde cero y chocar con recursos existentes.

`NIMBUSCORE_SCRIPT_DRY_RUN`:

```text
false:
  ejecuta realmente SSH y scripts.

true:
  solo imprime el comando preparado y marca el job como procesado.
  no crea VLANs ni VMs.
```

La asignacion de VMs a workers es round-robin usando `NIMBUSCORE_COMPUTE_IPS`.

El inventario real de VMs se genera en el worker con los mismos valores usados para ejecutar los scripts. Queda guardado en:

```text
SCRIPT_RUNS/vm_inventory.json
```

El backend lo expone dentro de:

```text
GET /api/slices/{slice_id}
GET /api/slices/{slice_id}/inventory
GET /api/deployments/{job_id}
```

Cada VM queda registrada con:

```text
name:
  nombre real usado por QEMU.

worker_ip:
  worker donde se crea la VM.

vnc_port / vnc_target:
  puerto VNC real para noVNC o tuneles.

nics:
  ethX, VLAN, MAC, TAP, CIDR DHCP y namespace DHCP.

flavor / vcpus / ram_mb / disk_gb:
  recursos efectivos.

image_name / image_url / key_pair:
  imagen y llave usadas para cloud-init.
```

Si un nodo no tiene flavor o disco seleccionado, se usa el default efectivo:

```text
1 vCPU | 2048 MB RAM | 20 GB disco
```

La asignacion de puertos VNC se guarda en:

```text
SCRIPT_RUNS/vnc_allocations.json
```

La asignacion de VLANs y subredes se guarda en:

```text
SCRIPT_RUNS/network_allocations.json
```

Si borras `SCRIPT_RUNS/` mientras siguen VMs prendidas, los contadores vuelven a empezar desde `NIMBUSCORE_VNC_BASE`, `NIMBUSCORE_VLAN_BASE` y `NIMBUSCORE_CIDR_BASE`; podrias chocar con VNC, VLANs o rangos DHCP existentes.

## Flujo De Creacion Desde El Frontend

En el flujo actual de la interfaz, `configurar-slice.html` crea **plantillas**, no VMs.

```text
Guardar cambios
  -> valida que cada VM tenga flavor, disco, imagen y par de llaves
  -> POST /api/slice-templates
  -> guarda la plantilla en BACKEND_DATA/slice_templates.json
```

Desde `slices.html`, cada plantilla tiene dos acciones:

```text
Ojito:
  abre configurar-slice.html en modo revision.

Asignar:
  valida que la plantilla este completa.
  abre asignar-slice.html con la plantilla seleccionada.
```

En `asignar-slice.html` se elige un curso. Por ahora los cursos son fijos y persistidos en `BACKEND_DATA/academic.json`:

```text
TEL141:
  3 alumnos

TEL142:
  3 alumnos
```

Al asignar una plantilla a un curso:

```text
Crear slices por alumno
  -> POST /api/slice-templates/{template_id}/assign-to-course
  -> crea 1 slice por cada alumno del curso
  -> guarda los slices en BACKEND_DATA/slices.json
  -> no despliega VMs todavia
```

Ejemplo: si la plantilla `Lab DHCP` se asigna a un curso con 3 alumnos, se crean 3 slices con la misma configuracion:

```text
Lab DHCP - Ana Torres
Lab DHCP - Bruno Diaz
Lab DHCP - Carla Rojas
```

El despliegue real con QEMU/KVM sigue existiendo en el backend, pero queda separado del guardado de plantillas. Cuando se llame al despliegue de un slice ya asignado:

```text
POST /api/slices/{slice_id}/deploy
```

El backend genera un job JSON en el volumen compartido `/jobs`. El worker detecta ese archivo y ejecuta el comando correspondiente.

En `detalle-curso.html` hay dos acciones reales por slice:

```text
Desplegar:
  -> POST /api/slices/{slice_id}/deploy
  -> el backend crea un job create_topology
  -> el worker ejecuta create_linear_topology.sh o create_ring_topology.sh
  -> se crean VLANs, DHCP, TAPs y VMs QEMU.

Apagar:
  -> POST /api/slices/{slice_id}/destroy
  -> el backend crea un job destroy_topology usando el inventario real del slice
  -> el worker entra por SSH al worker de cada VM
  -> ejecuta delete_vm.sh para matar QEMU, quitar TAPs de OVS y borrar el disco qcow2 de la VM.
```

La accion `Apagar` no borra la plantilla ni la asignacion academica. Solo destruye las VMs del slice desplegado. El slice queda en estado `DESTRUIDO` y puede volver a desplegarse despues.

Ejemplo de comando generado:

```bash
ssh ubuntu@10.0.10.4 \
  NIMBUSCORE_HEADNODE_LOCAL=true \
  NIMBUSCORE_REMOTE_SCRIPTS_DIR='/home/ubuntu/script_runner' \
  NIMBUSCORE_COMPUTE_IPS='10.0.10.1,10.0.10.2,10.0.10.3' \
  NIMBUSCORE_OVS_UPLINKS='ens4' \
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

En `server4` crea:

```text
br-int
  vlan100 internal tag=100
  veth-h-100 tag=100
  ens4 como trunk fisico, si NIMBUSCORE_OVS_UPLINKS=ens4

namespace:
  dhcp-ns-vlan100
  veth-ns-100

gateway:
  192.168.10.1

dnsmasq:
  entrega IPs 192.168.10.10 - 192.168.10.100
  guarda leases en /var/lib/misc/dnsmasq-dhcp-ns-vlan100.leases
  guarda logs en /var/log/dnsmasq-dhcp-ns-vlan100.log
```

Modelo DHCP actual:

```text
1 VLAN = 1 namespace DHCP = 1 dnsmasq
```

No se crea un namespace por slice completo. Como NimbusCore usa una VLAN por enlace logico, cada enlace del slice tiene su propio namespace y su propio servicio DHCP.

### routing_networks.sh

```bash
./routing_networks.sh <VLAN_A> <VLAN_B>
```

Habilita forwarding entre dos VLANs en `server4` con iptables. Esto permite que las redes creadas se comuniquen pasando por el head node.

### create_vm.sh

```bash
./create_vm.sh <VM_NAME> <OVS_NAME> <VNC_PORT> <VLAN_1> [VLAN_2 ...]
```

Ejemplo:

```bash
./create_vm.sh slice1-vm2 br-int 5902 100 101
```

En el worker correspondiente hace:

```text
1. Descarga o reutiliza la imagen base seleccionada.
2. La cachea en /var/lib/qemu/images/<imagen>.qcow2.
3. Crea un disco overlay qcow2 por VM con el tamano definido por disco.
4. Busca la llave publica seleccionada en /home/ubuntu/nimbuscore-keys/<key>.pub.
5. Genera un ISO cloud-init con llave publica, usuario de consola y network-config.
6. Crea una interfaz TAP por VLAN.
7. Genera una MAC deterministica por VM, interfaz y VLAN.
8. Agrega cada TAP a br-int con tag VLAN y guarda metadata en OVS.
9. Arranca qemu-system-x86_64 con VNC y el ISO cloud-init.
```

El `network-config` de cloud-init configura DHCP en todas las interfaces de la VM usando las MACs generadas por NimbusCore. Esto es importante para Ubuntu/Debian cloud images: sin esa configuracion, algunas imagenes solo levantan DHCP en la primera NIC y las interfaces adicionales pueden quedarse sin IP.

Las MACs no las deja al azar QEMU. Se generan con hash usando:

```text
NIMBUSCORE_MAC_SALT + VM_NAME + indice_de_interfaz + VLAN_ID
```

Formato:

```text
52:54:00:xx:xx:xx
```

Ejemplo conceptual:

```text
slice1-vm1 eth0 VLAN 100 -> 52:54:00:<hash>
slice1-vm2 eth0 VLAN 100 -> 52:54:00:<hash>
slice1-vm2 eth1 VLAN 101 -> 52:54:00:<hash>
```

El puerto OVS queda con metadata:

```text
external_ids:vm=<VM_NAME>
external_ids:vlan=<VLAN_ID>
external_ids:mac=<MAC_ADDR>
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

Por eso la VM intermedia puede hacer ping a ambos lados. Eso es esperado: tiene una interfaz por cada enlace/VLAN donde participa.

Ejemplo real observado en una VM intermedia:

```text
eth0 -> 192.168.11.39/24  VLAN 101
eth1 -> 192.168.10.57/24  VLAN 100
```

Importante: actualmente `server4` tambien puede enrutar entre VLANs usando `routing_networks.sh`. Entonces el trafico entre extremos puede pasar por el head node:

```text
vm1 -> server4 -> vm3
```

Si se requiere una topologia estricta donde el trafico pase por la VM intermedia, habria que cambiar el modelo:

```text
server4:
  solo DHCP/red por enlace
  sin forwarding entre VLANs

vm intermedia:
  ip_forward=1
  rutas/reglas para reenviar trafico
```

## Transporte Entre OVS

Los scripts crean TAPs y VLANs en los bridges OVS, pero asumen que existe conectividad L2 para transportar esas VLANs entre `server4` y los workers.

Si `br-int` de `server4` y `br-int` de `server1/server2/server3` no comparten un trunk, VXLAN, GRE o una configuracion equivalente, las VMs pueden crearse pero no necesariamente recibiran DHCP ni tendran conectividad con las redes del head node.

En el laboratorio actual se usa `ens4` como troncal L2. Esa interfaz debe estar dentro de `br-int` tanto en `server4` como en los workers. `ens3` queda fuera porque es la interfaz de gestion/SSH.

Los scripts intentan agregar las interfaces definidas en `NIMBUSCORE_OVS_UPLINKS`, pero tambien puedes validar o corregir manualmente asi:

```bash
sudo ip link set br-int up
sudo ip link set ens4 up
sudo ovs-vsctl --may-exist add-port br-int ens4
```

En `server4`, esto debe mostrar un puerto `ens4`:

```bash
sudo ovs-vsctl list port ens4
sudo ovs-vsctl show
```

Si `sudo ovs-vsctl list port ens4` responde `no row "ens4" in table Port`, el DHCP puede llegar hasta la interfaz fisica de `server4` pero no entrar al bridge ni al namespace DHCP.

Validaciones utiles:

```bash
# workers
sudo ovs-vsctl show
sudo ovs-vsctl list port | grep -E "name|tag|external_ids"
ps aux | grep qemu

# server4
sudo ovs-vsctl show
sudo ovs-vsctl list port ens4
ip a | grep vlan
sudo ip netns list
sudo iptables -L FORWARD -n -v
```

## Diagnostico DHCP

Si algunas VMs reciben IP y otras no, revisa en este orden:

```bash
# En server4: confirmar namespaces DHCP
sudo ip netns list

# En server4: confirmar direccion y socket DHCP del namespace
sudo ip netns exec dhcp-ns-vlan100 ip -br addr
sudo ip netns exec dhcp-ns-vlan100 ss -lunp

# En server4: confirmar dnsmasq y leases de una VLAN
sudo cat /var/lib/misc/dnsmasq-dhcp-ns-vlan100.leases
sudo tail -n 80 /var/log/dnsmasq-dhcp-ns-vlan100.log

# En workers: confirmar TAPs, tags VLAN y MACs
sudo ovs-vsctl list port | grep -E "name|tag|external_ids"
ip link | grep tap

# En worker: ver si salen DHCP Discover por la troncal
sudo tcpdump -eni ens4 'vlan 100 and (udp port 67 or udp port 68)'

# En server4: ver si llegan DHCP Discover por la troncal
sudo tcpdump -eni ens4 'vlan 100 and (udp port 67 or udp port 68)'

# En server4: ver si llegan al namespace DHCP
sudo ip netns exec dhcp-ns-vlan100 tcpdump -eni veth-ns-100 'udp port 67 or udp port 68'

# En la VM Ubuntu/Debian: pedir DHCP manual si necesitas probar
sudo dhclient -v eth0
sudo dhclient -v eth1
ip addr
```

Interpretacion rapida:

```text
La VM no aparece en leases:
  el DHCP discover no esta llegando al dnsmasq. Revisa trunk/VXLAN/GRE entre server4 y el worker, o tags VLAN en OVS.

Ves DHCP Discover en ens4 de server4 pero no en veth-ns-100:
  normalmente ens4 no esta agregado a br-int en server4, br-int esta abajo, o el puerto veth-h-100 no esta taggeado correctamente.

No ves DHCP Discover en ens4 de server4:
  revisa el transporte L2 entre worker y server4: switch, VLAN permitida, trunk, cableado virtual o ens4 en el worker.

La VM aparece en leases, pero no tiene IP:
  revisa cloud-init/netplan dentro de la VM. En VMs nuevas, NimbusCore ya genera network-config para todas las NICs.

Solo falla una interfaz secundaria:
  probablemente era una VM creada antes del ajuste de network-config. Recreala para que cloud-init configure DHCP en todas las NICs.
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

nodos[].configuracion.flavor:
  define vCPU y RAM de cada VM.

nodos[].configuracion.disco:
  define el tamano de disco qcow2 de cada VM.
```

El backend transforma esos datos en:

```text
SLICE_NAME
N_VMS
VLAN_BASE
VNC_BASE
CIDR_BASE
VM_SPEC por nodo:
  vcpus, ram_mb, disk_gb
```

`VLAN_BASE`, `VNC_BASE` y `CIDR_BASE` no vienen del frontend. Se asignan automaticamente desde variables y estado del worker:

```text
NIMBUSCORE_VLAN_BASE=100
NIMBUSCORE_VLAN_BLOCK_SIZE=100 como bloque de VLANs por slice
NIMBUSCORE_VNC_BASE=5901 como inicio global
NIMBUSCORE_VNC_BLOCK_SIZE=100 como bloque por slice
NIMBUSCORE_CIDR_BASE=10 como tercer octeto inicial: 192.168.10.0/24
NIMBUSCORE_CIDR_BLOCK_SIZE=20 como bloque de subredes /24 por slice
```

Catalogo de flavors usado por backend/worker:

```text
m1.tiny   -> 1 vCPU  | 512 MB  RAM
m1.small  -> 1 vCPU  | 2048 MB RAM
m1.medium -> 2 vCPUs | 4096 MB RAM
m1.large  -> 4 vCPUs | 8192 MB RAM
m1.xlarge -> 8 vCPUs | 16384 MB RAM
```

Catalogo de discos usado desde el frontend:

```text
20 GB
50 GB
100 GB
200 GB
500 GB
```

La fuente de verdad para CPU/RAM es `flavor`. La fuente de verdad para disco es `disco`.

Internamente, el worker pasa estos valores hacia los scripts de topologia con:

```text
NIMBUSCORE_TOPOLOGY_VM_SPECS='vcpus:ram_mb:disk_gb;vcpus:ram_mb:disk_gb;...'
```

Luego `create_linear_topology.sh` o `create_ring_topology.sh` exportan por VM:

```text
NIMBUSCORE_VM_VCPUS
NIMBUSCORE_VM_RAM_MB
NIMBUSCORE_VM_DISK_GB
```

La imagen seleccionada tambien viaja por VM:

```text
NIMBUSCORE_TOPOLOGY_IMAGE_SPECS='image_name|image_url|download_method;image_name|image_url|download_method;...'
```

Catalogo de imagenes inicial:

```text
cirros          -> https://download.cirros-cloud.net/0.6.2/cirros-0.6.2-x86_64-disk.img
ubuntu-22       -> https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img
ubuntu-20       -> https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-amd64.img
ubuntu-20-drive -> Google Drive con wget --no-check-certificate
debian-12       -> https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2
```

Para usar imagenes desde GitHub o Drive, la regla es que el backend debe tener una URL de descarga directa. Lo mas practico es publicar imagenes grandes como assets de GitHub Releases, GitHub LFS o un enlace publico directo equivalente. Google Drive puede funcionar con `drive.usercontent.google.com` y `download_method=wget-no-check-certificate`; un enlace normal de vista previa no siempre sirve para `wget`/`curl`.

El flujo actual no sube archivos desde el navegador. El boton de subir imagen conserva el nombre en la UI, pero todavia no envia el binario al backend ni a un almacenamiento externo.

El par de llaves seleccionado tambien viaja por VM:

```text
NIMBUSCORE_TOPOLOGY_KEYPAIR_SPECS='key-dev;key-prod;key-testing;...'
```

Luego `create_linear_topology.sh` o `create_ring_topology.sh` exportan por VM:

```text
NIMBUSCORE_KEYPAIR_NAME
NIMBUSCORE_PUBLIC_KEY_B64
```

`create_vm.sh` usa primero `NIMBUSCORE_PUBLIC_KEY_B64`. Si no llega esa variable, busca la llave publica en:

```text
/home/ubuntu/nimbuscore-keys/<NIMBUSCORE_KEYPAIR_NAME>.pub
```

Si existe, crea un ISO cloud-init y lo adjunta a QEMU. Si no existe, muestra un warning y crea la VM sin inyectar llave. Para hacer que la ausencia de llave sea un error, se puede exportar:

```text
NIMBUSCORE_REQUIRE_KEYPAIR=true
```

## Parametros Del Frontend Todavia No Usados

Estos campos pueden existir o aparecer en la UI, pero todavia no controlan la VM real:

```text
zona de disponibilidad:
  no decide worker todavia.
  el worker usado sale de NIMBUSCORE_COMPUTE_IPS.

reglas de seguridad:
  no se aplican por VM todavia.
  el acceso SSH desde local depende del salto por server4 y de la conectividad OVS/VLAN, no de un security group implementado por NimbusCore.

nodos y enlaces manuales:
  se guardan como parte del slice, pero el worker actual despliega segun topologias[].type y topologias[].count.
```

Variables soportadas por `create_vm.sh`:

```text
NIMBUSCORE_BASE_IMAGE_URL
NIMBUSCORE_BASE_IMAGE_NAME
NIMBUSCORE_QEMU_IMAGE_DIR
NIMBUSCORE_BASE_IMAGE_PATH
NIMBUSCORE_VM_RAM_MB
NIMBUSCORE_VM_VCPUS
NIMBUSCORE_VM_DISK_GB
NIMBUSCORE_MAC_SALT
NIMBUSCORE_KEYPAIR_NAME
NIMBUSCORE_KEYPAIR_DIR
NIMBUSCORE_PUBLIC_KEY_PATH
NIMBUSCORE_PUBLIC_KEY_B64
NIMBUSCORE_CLOUD_INIT_DIR
NIMBUSCORE_REQUIRE_KEYPAIR
NIMBUSCORE_CONSOLE_USER
NIMBUSCORE_CONSOLE_PASSWORD
NIMBUSCORE_ENABLE_PASSWORD_LOGIN
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
GET    /api/cursos/{course_id}
GET    /api/cursos/{course_id}/alumnos
GET    /api/images
POST   /api/images
GET    /api/keypairs
POST   /api/keypairs
GET    /api/slice-templates
POST   /api/slice-templates
GET    /api/slice-templates/{template_id}
PUT    /api/slice-templates/{template_id}
DELETE /api/slice-templates/{template_id}
POST   /api/slice-templates/{template_id}/assign-to-course
GET    /api/slices
POST   /api/slices
GET    /api/slices/{slice_id}
GET    /api/slices/{slice_id}/inventory
PUT    /api/slices/{slice_id}
DELETE /api/slices/{slice_id}
POST   /api/slices/{slice_id}/deploy
GET    /api/deployments/{job_id}
POST   /api/slices/{slice_id}/start
POST   /api/slices/{slice_id}/stop
POST   /api/slices/{slice_id}/restart
POST   /api/slices/{slice_id}/destroy
```
