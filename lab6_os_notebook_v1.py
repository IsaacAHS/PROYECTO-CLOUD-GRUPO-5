#!/usr/bin/env python
# coding: utf-8

# # Laboratorio 6 - Despliegue de slices en OpenStack
# 
# Notebook unificado para desplegar un *slice* paso a paso usando las APIs REST de
# **Keystone**, **Nova** y **Neutron**.
# 
# - **Act 1**: red interna del *slice* (sin internet).
# - **Act 2**: red *provider* compartida (`--share --flat`) que, en combinación
#   con `dnsmasq` en un *network namespace* y NAT con `iptables` en el *headnode*,
#   da internet a las VMs.
# 
# ## Flujo
# 
# | # | Paso                                       | Sección |
# |---|--------------------------------------------|---------|
# | 1 | Obtener token administrativo               | 1 |
# | 2 | Crear proyecto                             | 2 |
# | 3 | Crear/asociar usuario propietario          | 3 |
# | 4 | Asignar roles sobre el proyecto            | 4 |
# | 5 | Obtener token *scoped* para el proyecto    | 5 |
# | 6 | Crear red(es)                              | 6 |
# | 7 | Crear subred(es)                           | 7 |
# | 8 | Crear puerto(s)                            | 8 |
# | 9 | Crear instancia(s)                         | 9 |
# |10 | Obtener URL de consola remota              | 0 |

# ## Setup inicial
# 
# Ejecute la siguiente celda **una sola vez** para instalar las dependencias.

# In[ ]:


# Descomente si necesita instalar las dependencias
!pip install python-dotenv requests

# ## Configuración — Credenciales
# 
# Las credenciales se cargan del archivo `.env` si existe. Si prefiere editarlas
# directamente acá, reemplace los valores por defecto.
# 
# Variables esperadas en `.env`:
# 
# ```
# ACCESS_NODE_IP=<ip del nodo de acceso>
# KEYSTONE_PORT=5000
# NOVA_PORT=8774
# NEUTRON_PORT=9696
# DOMAIN_ID=<id del dominio>
# ADMIN_PROJECT_ID=<id del proyecto admin / cloud_admin>
# ADMIN_USER_ID=<id del usuario admin>
# ADMIN_USER_PASSWORD=<contraseña del admin>
# COMPUTE_API_VERSION=2.87
# ADMIN_ROLE_ID=<id del rol admin>
# ```

# In[ ]:


import os
import sys
import json
import requests
from dotenv import load_dotenv

load_dotenv()

KEYSTONE_PORT       = os.getenv("KEYSTONE_PORT",       "55000")
NOVA_PORT           = os.getenv("NOVA_PORT",           "58774")
NEUTRON_PORT        = os.getenv("NEUTRON_PORT",        "59696")
GLANCE_PORT         = os.getenv("GLANCE_PORT",         "59292")
ACCESS_NODE_IP      = os.getenv("ACCESS_NODE_IP",      "localhost")
ADMIN_USER_ID       = os.getenv("ADMIN_USER_ID",       "72d60bd76f254eed9c9ea9a86c35df48")
ADMIN_USER_PASSWORD = os.getenv("ADMIN_USER_PASSWORD", "66c5f106f03328bbb47bd5ec609c320e")
DOMAIN_ID           = os.getenv("DOMAIN_ID",           "ff80f00b054f4c4abd3a00d3de1bf48f")
ADMIN_PROJECT_ID    = os.getenv("ADMIN_PROJECT_ID",    "490934a931634a3ead678e446ec662d7")
ADMIN_ROLE_ID       = os.getenv("ADMIN_ROLE_ID",       "6923937f568d47ccbb178d7b14fcd1a2")
COMPUTE_API_VERSION = os.getenv("COMPUTE_API_VERSION", "2.87")

KEYSTONE_ENDPOINT = f"http://{ACCESS_NODE_IP}:{KEYSTONE_PORT}/v3"
NOVA_ENDPOINT     = f"http://{ACCESS_NODE_IP}:{NOVA_PORT}/v2.1"
NEUTRON_ENDPOINT  = f"http://{ACCESS_NODE_IP}:{NEUTRON_PORT}/v2.0"
GLANCE_ENDPOINT   = f"http://{ACCESS_NODE_IP}:{GLANCE_PORT}/v2.0"

# Verificación rápida (no muestra la contraseña)
print("Endpoints:")
print("  KEYSTONE :", KEYSTONE_ENDPOINT)
print("  NOVA     :", NOVA_ENDPOINT)
print("  NEUTRON  :", NEUTRON_ENDPOINT)
print("  GLANCE   :", GLANCE_ENDPOINT)
print()
missing = [k for k, v in {
    "ACCESS_NODE_IP": ACCESS_NODE_IP, "DOMAIN_ID": DOMAIN_ID,
    "ADMIN_PROJECT_ID": ADMIN_PROJECT_ID, "ADMIN_USER_ID": ADMIN_USER_ID,
    "ADMIN_USER_PASSWORD": ADMIN_USER_PASSWORD, "ADMIN_ROLE_ID": ADMIN_ROLE_ID,
}.items() if not v]
if missing:
    print("!  Faltan variables:", ", ".join(missing))
else:
    print("OK. Todas las credenciales cargadas.")

# ## SDK — funciones REST de bajo nivel
# 
# Una función por *endpoint* de OpenStack. Construyen URL + headers + body y
# devuelven el objeto `Response` de `requests` sin interpretarlo.

# In[ ]:


# ============================== KEYSTONE ==================================

def password_authentication_with_scoped_authorization(auth_endpoint, user_id, password, domain_id, project_id):
    """POST /auth/tokens — autenticación por contraseña con alcance sobre un proyecto. Éxito = 201."""
    url = auth_endpoint + "/auth/tokens"
    data = {"auth": {
        "identity": {"methods": ["password"],
                     "password": {"user": {"id": user_id,
                                           "domain": {"id": domain_id},
                                           "password": password}}},
        "scope": {"project": {"domain": {"id": domain_id}, "id": project_id}},
    }}
    return requests.post(url=url, data=json.dumps(data),
                         headers={"Content-type": "application/json"})

def token_authentication_with_scoped_authorization(auth_endpoint, token, domain_id, project_id):
    """POST /auth/tokens — re-scoping a partir de un token existente. Éxito = 201."""
    url = auth_endpoint + "/auth/tokens"
    data = {"auth": {
        "identity": {"methods": ["token"], "token": {"id": token}},
        "scope": {"project": {"domain": {"id": domain_id}, "id": project_id}},
    }}
    return requests.post(url=url, data=json.dumps(data),
                         headers={"Content-type": "application/json"})

def create_project(auth_endpoint, token, domain_id, project_name, project_description=""):
    """POST /projects — crea un proyecto. Éxito = 201."""
    url = auth_endpoint + "/projects"
    headers = {"Content-type": "application/json", "X-Auth-Token": token}
    data = {"project": {"name": project_name,
                        "description": project_description,
                        "domain_id": domain_id}}
    return requests.post(url=url, headers=headers, data=json.dumps(data))

def create_user(auth_endpoint, token, domain_id, user_name, password,
                default_project_id=None, enabled=True):
    """POST /users — crea un usuario. Éxito = 201."""
    url = auth_endpoint + "/users"
    headers = {"Content-type": "application/json", "X-Auth-Token": token}
    user = {"name": user_name, "domain_id": domain_id,
            "password": password, "enabled": enabled}
    if default_project_id:
        user["default_project_id"] = default_project_id
    return requests.post(url=url, headers=headers, data=json.dumps({"user": user}))

def assign_role_to_user_on_project(auth_endpoint, token, project_id, user_id, role_id):
    """PUT /projects/{p}/users/{u}/roles/{r} — asigna rol. Éxito = 204."""
    url = (auth_endpoint + "/projects/" + project_id +
           "/users/" + user_id + "/roles/" + role_id)
    headers = {"Content-type": "application/json", "X-Auth-Token": token}
    return requests.put(url=url, headers=headers)


# ============================== NOVA ======================================

def create_server(nova_endpoint, token, name, flavor_id, image_id, networks=None):
    """POST /servers — crea una VM. Éxito = 202 (asíncrono)."""
    url = nova_endpoint + "/servers"
    headers = {"Content-type": "application/json", "X-Auth-Token": token}
    data = {"server": {"name": name, "flavorRef": flavor_id,
                       "imageRef": image_id, "networks": networks}}
    return requests.post(url=url, headers=headers, data=json.dumps(data))

def get_server_console(nova_endpoint, token, server_id, compute_api_version):
    """POST /servers/{id}/remote-consoles — URL noVNC. Éxito = 200."""
    url = nova_endpoint + "/servers/" + server_id + "/remote-consoles"
    headers = {"Content-type": "application/json", "X-Auth-Token": token,
               "OpenStack-API-Version": "compute " + compute_api_version}
    data = {"remote_console": {"protocol": "vnc", "type": "novnc"}}
    return requests.post(url=url, headers=headers, data=json.dumps(data))


# ============================== NEUTRON ===================================

def create_network(auth_endpoint, token, name,
                   shared=False, provider_network_type=None,
                   provider_physical_network=None, router_external=False,
                   port_security_enabled=False):
    """POST /networks — crea una red. Para red provider: shared=True,
    provider_network_type='flat', provider_physical_network='<mapping>'.
    Éxito = 201."""
    url = auth_endpoint + "/networks"
    headers = {"Content-type": "application/json", "X-Auth-Token": token}
    network = {"name": name, "port_security_enabled": bool(port_security_enabled)}
    if shared:
        network["shared"] = True
    if provider_network_type:
        network["provider:network_type"] = provider_network_type
    if provider_physical_network:
        network["provider:physical_network"] = provider_physical_network
    if router_external:
        network["router:external"] = True
    return requests.post(url=url, headers=headers, data=json.dumps({"network": network}))

def create_subnet(auth_endpoint, token, network_id, name, ip_version, cidr,
                  enable_dhcp=False, gateway_ip=None,
                  allocation_pools=None, dns_nameservers=None):
    """POST /subnets — crea una subred. Éxito = 201."""
    url = auth_endpoint + "/subnets"
    headers = {"Content-type": "application/json", "X-Auth-Token": token}
    subnet = {"network_id": network_id, "name": name,
              "enable_dhcp": bool(enable_dhcp), "ip_version": int(ip_version),
              "cidr": cidr, "gateway_ip": gateway_ip}
    if allocation_pools is not None:
        subnet["allocation_pools"] = allocation_pools
    if dns_nameservers is not None:
        subnet["dns_nameservers"] = dns_nameservers
    return requests.post(url=url, headers=headers, data=json.dumps({"subnet": subnet}))

def create_port(auth_endpoint, token, name, network_id, project_id,
                fixed_ips=None, port_security_enabled=False):
    """POST /ports — crea un puerto. Éxito = 201."""
    url = auth_endpoint + "/ports"
    headers = {"Content-type": "application/json", "X-Auth-Token": token}
    port = {"name": name, "tenant_id": project_id, "network_id": network_id,
            "port_security_enabled": bool(port_security_enabled)}
    if fixed_ips is not None:
        port["fixed_ips"] = fixed_ips
    return requests.post(url=url, headers=headers, data=json.dumps({"port": port}))

print("SDK cargado:", 12, "funciones")

# ## Slice functions
# 
# Envuelven al SDK: validan el código HTTP esperado, en caso de error imprimen
# `status_code` + cuerpo de la respuesta a `stderr`, y devuelven solo el dato útil
# (id o diccionario).

# In[ ]:


def _check(r, expected, op):
    """Imprime status y body a stderr si la respuesta no es la esperada."""
    if r.status_code != expected:
        print(f"[{op}] HTTP {r.status_code}: {r.text}", file=sys.stderr)
        return False
    return True


def sf_get_admin_token():
    r = password_authentication_with_scoped_authorization(
        KEYSTONE_ENDPOINT, ADMIN_USER_ID, ADMIN_USER_PASSWORD, DOMAIN_ID, ADMIN_PROJECT_ID)
    return r.headers["X-Subject-Token"] if _check(r, 201, "get_admin_token") else ""

def sf_get_token_for_project(project_id, admin_token):
    r = token_authentication_with_scoped_authorization(
        KEYSTONE_ENDPOINT, admin_token, DOMAIN_ID, project_id)
    return r.headers["X-Subject-Token"] if _check(r, 201, "get_token_for_project") else ""

def sf_create_project(admin_token, name, description=""):
    r = create_project(KEYSTONE_ENDPOINT, admin_token, DOMAIN_ID, name, description)
    return r.json()["project"]["id"] if _check(r, 201, "create_project") else ""

def sf_create_user(admin_token, user_name, password, default_project_id=None):
    r = create_user(KEYSTONE_ENDPOINT, admin_token, DOMAIN_ID,
                    user_name, password, default_project_id=default_project_id)
    return r.json()["user"]["id"] if _check(r, 201, "create_user") else ""

def sf_assign_role(admin_token, project_id, user_id, role_id):
    r = assign_role_to_user_on_project(KEYSTONE_ENDPOINT, admin_token, project_id, user_id, role_id)
    return 1 if _check(r, 204, "assign_role") else 0

def sf_create_network(token, name, shared=False, provider_type=None,
                      provider_physical_network=None, router_external=False,
                      port_security_enabled=False):
    r = create_network(NEUTRON_ENDPOINT, token, name,
                       shared=shared,
                       provider_network_type=provider_type,
                       provider_physical_network=provider_physical_network,
                       router_external=router_external,
                       port_security_enabled=port_security_enabled)
    return r.json()["network"]["id"] if _check(r, 201, "create_network") else ""

def sf_create_subnet(token, name, network_id, cidr, ip_version=4,
                     enable_dhcp=False, gateway_ip=None,
                     allocation_pools=None, dns_nameservers=None):
    r = create_subnet(NEUTRON_ENDPOINT, token, network_id, name, ip_version, cidr,
                      enable_dhcp=enable_dhcp, gateway_ip=gateway_ip,
                      allocation_pools=allocation_pools, dns_nameservers=dns_nameservers)
    return r.json()["subnet"]["id"] if _check(r, 201, "create_subnet") else ""

def sf_create_port(token, name, network_id, project_id, fixed_ips=None, port_security_enabled=False):
    r = create_port(NEUTRON_ENDPOINT, token, name, network_id, project_id,
                    fixed_ips=fixed_ips, port_security_enabled=port_security_enabled)
    return r.json()["port"]["id"] if _check(r, 201, "create_port") else ""

def sf_create_instance(token, image_id, flavor_id, name, port_list):
    ports = [{"port": pid} for pid in port_list]
    r = create_server(NOVA_ENDPOINT, token, name, flavor_id, image_id, ports)
    return r.json() if _check(r, 202, "create_instance") else {}

def sf_get_console_url(admin_token, instance_id):
    r = get_server_console(NOVA_ENDPOINT, admin_token, instance_id, COMPUTE_API_VERSION)
    return r.json()["remote_console"]["url"] if _check(r, 200, "get_console_url") else ""

print("Slice functions cargadas: 10 funciones")

# ## Estado del despliegue
# 
# Las variables de abajo guardan los IDs/tokens conforme se ejecutan los pasos.
# Persisten entre celdas durante toda la sesión del kernel.

# In[ ]:


admin_token       = ""
project_id        = ""
new_user_id       = ""
scoped_token      = ""
networks_created  = {}
subnets_created   = {}
ports_created     = {}
instances_created = {}

# ## Paso 1: Token administrativo
# 
# Autenticación con contraseña + alcance sobre el proyecto `admin_cloud`.
# El token viene en la cabecera `X-Subject-Token` y se usa en todos los pasos.

# #### NOTA: Ninguna API de openstack le responderá sin haberse autenticado primero ante KEYSTONE

# In[ ]:


admin_token = sf_get_admin_token()
print("admin_token:", (admin_token[:24] + "…") if admin_token else "(vacío)")

# ## Paso 2: Crear proyecto (slice)
# 
# Keystone genera el UUID automáticamente.

# In[ ]:


# ----- Paso 2: proyecto (slice) ---------------------------------------------
SLICE_NAME          = "topo2_lab6"
SLICE_DESCRIPTION   = "topo2_lab6"

# In[ ]:


project_id = sf_create_project(admin_token, SLICE_NAME, SLICE_DESCRIPTION)
print("project_id:", project_id)

# ## Paso 3: Crear/asociar usuario propietario
# 
# Según el switch `CREATE_NEW_USER` de la celda de configuración, se crea un
# usuario nuevo o se reutiliza `EXISTING_USER_ID`.

# In[ ]:


# ----- Paso 3: usuario propietario ------------------------------------------
CREATE_NEW_USER     = True            # False = saltar la creación
NEW_USER_NAME       = "usuario_G5"
NEW_USER_PASSWORD   = "grupo5"
# Si CREATE_NEW_USER = False y quiere usar un usuario existente:
EXISTING_USER_ID    = ""

# In[ ]:


if CREATE_NEW_USER:
    new_user_id = sf_create_user(admin_token, NEW_USER_NAME, NEW_USER_PASSWORD,
                                 default_project_id=project_id)
else:
    new_user_id = EXISTING_USER_ID

print("new_user_id:", new_user_id or "(no se creó/asoció usuario)")

# ## Paso 4: Asignar roles sobre el proyecto
# 
# Siempre se asigna el rol **admin al usuario administrador** sobre el nuevo
# slice (imprescindible). Opcionalmente, también se asigna un rol al usuario
# creado en el paso 3.

# In[ ]:


# ----- Paso 4: roles --------------------------------------------------------
ASSIGN_ROLE_TO_USER = False           # asignar rol también al usuario del paso 3
EXTRA_ROLE_ID       = ""              # rol que se le asignará (member, admin, etc.)

# In[ ]:


# 4a. Rol admin al usuario admin (siempre)
status_admin = sf_assign_role(admin_token, project_id, ADMIN_USER_ID, ADMIN_ROLE_ID)
print("admin_user → status:", status_admin)

# 4b. Rol opcional al usuario del paso 3
if ASSIGN_ROLE_TO_USER and new_user_id and EXTRA_ROLE_ID:
    status_extra = sf_assign_role(admin_token, project_id, new_user_id, EXTRA_ROLE_ID)
    print("new_user   → status:", status_extra)

# ## Paso 5: Token scoped sobre el proyecto
# 
# Re-scoping del token admin para operar dentro del slice.
# Se usa en los pasos 6, 7, 8 y 9 cuando se trabaja con **Act 1**.
# 
# En **ACT2** se sigue usando el `admin_token` para crear los recursos provider (red de salida a internet).

# In[ ]:


scoped_token = sf_get_token_for_project(project_id, admin_token)
print("scoped_token:", (scoped_token[:24] + "…") if scoped_token else "(vacío)")

# ## Paso 6: Crear red
# 
# Use la variable `ACTIVITY` y `NETWORK_NAME` de la celda de configuración.
# Para crear varias redes, cambie esas variables y vuelva a ejecutar esta celda;
# los IDs se acumulan en `networks_created`.
# 
# | | Act 1 (tenant) | Act 2 (provider) |
# |---|---|---|
# | Token | `scoped_token` | `admin_token` |
# | Atributos | nombre | `shared=true`, `provider:network_type=flat`, `provider:physical_network=<physnet>` |
# | Visibilidad | solo el slice | compartida con todos |

# In[ ]:


# ----- Paso 6: red ----------------------------------------------------------
# Para ACT1: red self-service sin DHCP ni gateway

ACTIVITY            = "act1"
NETWORK_NAME        = "network_link1"
PROVIDER_PHYSNET    = "physnet0"
print("Configuración cargada para la actividad:", ACTIVITY)

# In[ ]:


# ----- Paso 6: red ----------------------------------------------------------
# Para ACT2: red externa provider con DHCP y gateway.
# La red provider ya existe, así que no se crea desde el script. Debe buscar su ID en Horizon

networks_created = {"external": "ID"}   # COMPLETAR EL ID

ACTIVITY            = "act2"
NETWORK_NAME        = "external"
PROVIDER_PHYSNET    = "physnet0"
print("Configuración cargada para la actividad:", ACTIVITY)
print("Redes:", networks_created)

# NO EJECUTAR LA CASILLA INFERIOR YA QUE LA NETWORK EXISTE

# In[ ]:


if ACTIVITY == "act1":
    nid = sf_create_network(scoped_token, NETWORK_NAME)
elif ACTIVITY == "act2":
    nid = sf_create_network(
        admin_token, NETWORK_NAME,
        shared=True,
        provider_type="flat",
        provider_physical_network=PROVIDER_PHYSNET,
        router_external=True,
    )
else:
    raise SystemExit(f"ACTIVITY inválida: {ACTIVITY!r}. Use 'act1' o 'act2'.")

if nid:
    networks_created[NETWORK_NAME] = nid
print(f"{NETWORK_NAME}: {nid}")
print("networks_created:", networks_created)

# ## Paso 7: Crear subred
# 
# La subred se crea sobre `networks_created[NETWORK_NAME]`. Para crear varias,
# edite las variables (`SUBNET_NAME`, `SUBNET_CIDR`, etc., y eventualmente
# `NETWORK_NAME` para apuntar a otra red) y vuelva a ejecutar esta celda.

# In[ ]:


# ----- Paso 7: subred -------------------------------------------------------
# Para ACT1: red self-service sin DHCP ni gateway

NETWORK_NAME        = "network_link1"               # Debe existir en el proyecto (creado en el paso 6)

SUBNET_NAME         = "subnet_link1"
SUBNET_CIDR         = "192.168.1.0/30"              # COMPLETAR LA X
SUBNET_IP_VERSION   = 4
SUBNET_GATEWAY      = None
SUBNET_DHCP         = False
SUBNET_ALLOC_POOLS  = None
SUBNET_DNS          = None

# In[ ]:


# ----- Paso 7: subred -------------------------------------------------------
# Para ACT2: red externa provider con DHCP y gateway.

NETWORK_NAME        = "external"                 # Ya existe, debe obtener el ID desde Horizon y cargarlo aqui:

SUBNET_NAME         = "external_subnet"          # Ya existe, bórrela desde Horizon y recreela con la configuración deseada, no olvide borrar los puertos dentro
SUBNET_CIDR         = "10.60.X.0/24"             # COMPLETAR LA X
SUBNET_IP_VERSION   = 4
SUBNET_GATEWAY      = "10.60.X.1"                # COMPLETAR LA X
SUBNET_DHCP         = True
SUBNET_ALLOC_POOLS  = [{"start": "10.60.X.10", "end": "10.60.X.250"}]              # COMPLETAR LA X
SUBNET_DNS          = ["8.8.8.8"]

# In[ ]:


network_id = networks_created.get(NETWORK_NAME, "")
if not network_id:
    raise SystemExit(f"No hay red registrada con nombre {NETWORK_NAME!r}. "
                     f"Ejecute primero el paso 6.")

token_for_subnet = admin_token if ACTIVITY == "act2" else scoped_token

sid = sf_create_subnet(
    token_for_subnet, SUBNET_NAME, network_id, SUBNET_CIDR,
    ip_version=SUBNET_IP_VERSION,
    enable_dhcp=SUBNET_DHCP,
    gateway_ip=SUBNET_GATEWAY,
    allocation_pools=SUBNET_ALLOC_POOLS,
    dns_nameservers=SUBNET_DNS,
)
if sid:
    subnets_created[SUBNET_NAME] = sid
print(f"{SUBNET_NAME} → {sid}")
print("subnets_created:", subnets_created)

# ## Paso 8: Crear puerto
# 
# Un puerto por interfaz de VM. Para que una VM tenga internet, créele también
# un puerto sobre la red **ACT2** (provider).

# #### NOTA: PARA CREAR PUERTOS DEBE CONTAR CON EL TOKEN SCOPED DEL PROJECTO (VOLVER AL PASO 5)

# In[ ]:


# ----- Paso 8: puerto -------------------------------------------------------
# Para ACT1: red self-service sin DHCP ni gateway

NETWORK_NAME        = "network_link1"    # debe existir en el proyecto (creado en el paso 6)
SUBNET_NAME         = "subnet_link1"     # debe existir en el proyecto (creado en el paso 7)

PORT_NAME           = "port1_link1"
PORT_FIXED_IPS      = None               # ej. [{"ip_address": "10.60.10.20", "subnet_id": "<id>"}]

# In[ ]:


# ----- Paso 8: puerto -------------------------------------------------------
# Para ACT2: red externa provider con DHCP y gateway.

NETWORK_NAME        = "external"         # debe existir en el proyecto (creado en el paso 6)
SUBNET_NAME         = "external_subnet"  # debe existir en el proyecto (creado en el paso 7)

PORT_NAME           = "port1_ext"
PORT_FIXED_IPS      = None

# In[ ]:


network_id = networks_created.get(NETWORK_NAME, "")
if not network_id:
    raise SystemExit(f"No hay red registrada con nombre {NETWORK_NAME!r}.")

token_for_port = admin_token if ACTIVITY == "act2" else scoped_token

pid = sf_create_port(
    token_for_port, PORT_NAME, network_id, project_id,
    fixed_ips=PORT_FIXED_IPS,
)
if pid:
    ports_created[PORT_NAME] = pid
print(f"{PORT_NAME} → {pid}")
print("ports_created:", ports_created)

# ## Paso 9: Crear instancia (VM)
# 
# La VM se conecta usando los puertos creados en el paso 8. La variable
# `INSTANCE_PORT_NAMES` (en la configuración) lista los nombres de puerto a
# adjuntar; aquí los traducimos a IDs.

# #### NOTA: PARA CREAR INSTANCIAS DEBE CONTAR CON EL TOKEN SCOPED DEL PROJECTO (VOLVER AL PASO 5)

# In[ ]:


# ----- Paso 9: instancia ----------------------------------------------------
INSTANCE_NAME       = "instance_1"
IMAGE_ID            = "a61a8583-016c-4e19-9d45-e634a627c213"  # Busque el ID de la imagen cirros en horizon y cárguelo aquí
FLAVOR_ID           = "eb0bdaf9-4803-415c-8857-7956fefead50"  # Busque el ID de un flavor creado en horizon y cárguelo aquí

# Lista de puertos a adjuntar
INSTANCE_PORT_NAMES = ["port1_link1"]          # nombres de puertos definidos en `ports_created`

# In[ ]:


# Traducción nombres → ids
port_list = [ports_created[n] for n in INSTANCE_PORT_NAMES if n in ports_created]
missing = [n for n in INSTANCE_PORT_NAMES if n not in ports_created]
if missing:
    raise SystemExit(f"Falta crear los puertos: {missing}. Ejecute el paso 8 para cada uno.")

info = sf_create_instance(scoped_token, IMAGE_ID, FLAVOR_ID, INSTANCE_NAME, port_list)
if info:
    instances_created[INSTANCE_NAME] = info
print(json.dumps(info, indent=2)[:1200])

# ## Paso 10: URL de consola remota (noVNC)
# 
# Pegue la URL resultante en su navegador para ver la consola del SO de la VM (No olvide editar la IP y el PUERTO).

# In[ ]:


INSTANCE_NAME       = "instance_1"  # nombre de la instancia creada en el paso 9

# In[ ]:


instance_id = instances_created.get(INSTANCE_NAME, {}).get("server", {}).get("id", "")
if not instance_id:
    raise SystemExit(f"No hay instancia registrada con nombre {INSTANCE_NAME!r}.")

console_url = sf_get_console_url(admin_token, instance_id)
print("Consola:", console_url)

# ## Resumen del despliegue

# In[ ]:


print("=" * 70)
print(f"Slice            : {SLICE_NAME}  →  {project_id}")
print(f"Usuario nuevo    : {new_user_id or '(no se creó)'}")
print("-" * 70)
print("Redes creadas:")
for n, i in networks_created.items():    print(f"  {n:25s} {i}")
print("Subredes creadas:")
for n, i in subnets_created.items():     print(f"  {n:25s} {i}")
print("Puertos creados:")
for n, i in ports_created.items():       print(f"  {n:25s} {i}")
print("Instancias creadas:")
for n, info in instances_created.items():
    print(f"  {n:25s} {info.get('server', {}).get('id', '')}")
print("=" * 70)

# ----------------------------------------------CONTINUACION---------------------------------------------------------------------

# REDES FALTANTES

# In[ ]:


# ----- Red link2 -----
NETWORK_NAME = "network_link2"

nid = sf_create_network(scoped_token, NETWORK_NAME)
if nid:
    networks_created[NETWORK_NAME] = nid
print(f"Red creada: {NETWORK_NAME} ➔ ID: {nid}")

# In[ ]:


# ----- Red link3 -----
NETWORK_NAME = "network_link3"

nid = sf_create_network(scoped_token, NETWORK_NAME)
if nid:
    networks_created[NETWORK_NAME] = nid
print(f"Red creada: {NETWORK_NAME} ➔ ID: {nid}")

# SUBREDES FALTANTES

# In[ ]:


# ----- Subred link2 -----
NETWORK_NAME      = "network_link2"
SUBNET_NAME       = "subnet_link2"
SUBNET_CIDR       = "192.168.2.0/30"

network_id = networks_created.get(NETWORK_NAME, "")
sid = sf_create_subnet(
    scoped_token, SUBNET_NAME, network_id, SUBNET_CIDR,
    ip_version=4, enable_dhcp=False, gateway_ip=None
)
if sid:
    subnets_created[SUBNET_NAME] = sid
print(f"Subred direccionada: {SUBNET_NAME} ➔ ID: {sid}")

# In[ ]:


# ----- Subred link3 -----
NETWORK_NAME      = "network_link2" # Se asocia al link3
NETWORK_NAME      = "network_link3"
SUBNET_NAME       = "subnet_link3"
SUBNET_CIDR       = "192.168.3.0/30"

network_id = networks_created.get(NETWORK_NAME, "")
sid = sf_create_subnet(
    scoped_token, SUBNET_NAME, network_id, SUBNET_CIDR,
    ip_version=4, enable_dhcp=False, gateway_ip=None
)
if sid:
    subnets_created[SUBNET_NAME] = sid
print(f"Subred direccionada: {SUBNET_NAME} ➔ ID: {sid}")

# PUERTOS

# In[ ]:


# ----- port2_link1 (Extremo de VM2 en Enlace 1) -----
pid1 = sf_create_port(scoped_token, "port2_link1", networks_created.get("network_link1", ""), project_id)
if pid1: ports_created["port2_link1"] = pid1

# ----- port1_link2 (Extremo de VM2 en Enlace 2) -----
pid2 = sf_create_port(scoped_token, "port1_link2", networks_created.get("network_link2", ""), project_id)
if pid2: ports_created["port1_link2"] = pid2

print("Puertos de Instance 2 listos.")

# In[ ]:


# ----- port2_link2 (Extremo de VM3 en Enlace 2) -----
pid3 = sf_create_port(scoped_token, "port2_link2", networks_created.get("network_link2", ""), project_id)
if pid3: ports_created["port2_link2"] = pid3

# ----- port1_link3 (Extremo de VM3 en Enlace 3) -----
pid4 = sf_create_port(scoped_token, "port1_link3", networks_created.get("network_link3", ""), project_id)
if pid4: ports_created["port1_link3"] = pid4

print("Puertos de Instance 3 listos.")

# In[ ]:


# ----- port2_link3 (Extremo de VM1 en Enlace 3) -----
pid5 = sf_create_port(scoped_token, "port2_link3", networks_created.get("network_link3", ""), project_id)
if pid5: ports_created["port2_link3"] = pid5

print(f"Puerto de cierre generado ➔ ID: {pid5}")

# INSTANCIAS

# In[ ]:


# ----- Crear instance_2 -----
INSTANCE_NAME = "instance_2"
INSTANCE_PORT_NAMES = ["port2_link1", "port1_link2"]

port_list = [ports_created[n] for n in INSTANCE_PORT_NAMES if n in ports_created]
info_vm2 = sf_create_instance(scoped_token, IMAGE_ID, FLAVOR_ID, INSTANCE_NAME, port_list)

if info_vm2:
    instances_created[INSTANCE_NAME] = info_vm2
print(f"✅ ¡{INSTANCE_NAME} desplegada exitosamente!")

# In[ ]:


# ----- Crear instance_3 -----
INSTANCE_NAME = "instance_3"
INSTANCE_PORT_NAMES = ["port2_link2", "port1_link3"]

port_list = [ports_created[n] for n in INSTANCE_PORT_NAMES if n in ports_created]
info_vm3 = sf_create_instance(scoped_token, IMAGE_ID, FLAVOR_ID, INSTANCE_NAME, port_list)

if info_vm3:
    instances_created[INSTANCE_NAME] = info_vm3
print(f"✅ ¡{INSTANCE_NAME} desplegada exitosamente!")

# In[ ]:


# ----- Adjuntar port2_link3 a la instance_1 activa -----
inst1_id = instances_created.get("instance_1", {}).get("server", {}).get("id", "")
port_id  = ports_created.get("port2_link3", "")

if not inst1_id or not port_id:
    print("❌ Error: Verifica que existan en memoria 'instance_1' y 'port2_link3'")
else:
    url = f"{NOVA_ENDPOINT}/servers/{inst1_id}/os-interface"
    headers = {"Content-type": "application/json", "X-Auth-Token": scoped_token}
    data = {"interfaceAttachment": {"port_id": port_id}}

    r = requests.post(url, headers=headers, data=json.dumps(data))
    if r.status_code == 200:
        print("⚡ ¡Éxito! Red en anillo físicamente cerrada por backend.")
    else:
        print(f"❌ Fallo al adjuntar interfaz (HTTP {r.status_code}): {r.text}")

# EXTRACCION DE CONSOLAS
# 

# In[ ]:


# ----- noVNC Instance 2 -----
instance_id = instances_created.get("instance_2", {}).get("server", {}).get("id", "")
console_url = sf_get_console_url(admin_token, instance_id)
print(f"Consola INSTANCE_2: {console_url.replace('controller', '127.0.0.1')}")

# In[ ]:


# ----- noVNC Instance 3 -----
instance_id = instances_created.get("instance_3", {}).get("server", {}).get("id", "")
console_url = sf_get_console_url(admin_token, instance_id)
print(f"Consola INSTANCE_3: {console_url.replace('controller', '127.0.0.1')}")

# In[ ]:


# ----- Refrescar token de noVNC para Instance 1 -----
inst1_id = instances_created.get("instance_1", {}).get("server", {}).get("id", "")

if inst1_id:
    vnc_info = sf_get_console_url(admin_token, inst1_id)
    # Reemplazamos el nombre y el puerto directo a tu túnel
    url_lista = vnc_info.replace("controller:6080", "127.0.0.1:56080")
    print("=========================================================")
    print("🔗 NUEVO ENLACE INSTANCE 1:")
    print(url_lista)
    print("=========================================================")
else:
    print("❌ Error: No encuentro el ID de instance_1")

# In[ ]:


import requests

print("=========================================================")
print("🔍 PANEL DE CONTROL: ESTADO DE HARDWARE Y NUEVOS ENLACES")
print("=========================================================")

for vm_name in ["instance_1", "instance_2", "instance_3"]:
    vmid = instances_created.get(vm_name, {}).get("server", {}).get("id", "")

    if not vmid:
        print(f"❌ {vm_name.upper()}: No se encuentra su ID en la memoria local.")
        continue

    # 1. Consultamos a la API de Nova el estado real de la VM
    url_status = f"{NOVA_ENDPOINT}/servers/{vmid}"
    headers_status = {"X-Auth-Token": scoped_token}

    try:
        r_stat = requests.get(url_status, headers=headers_status, timeout=5)
        if r_stat.status_code == 200:
            real_status = r_stat.json().get("server", {}).get("status", "UNKNOWN")
            print(f"🖥️  {vm_name.upper():<10} ➔ Estado Real: {real_status}")

            # 2. Si está viva, le pedimos un token de video completamente nuevo
            if real_status == "ACTIVE":
                vnc_info = sf_get_console_url(admin_token, vmid)
                if vnc_info:
                    # Formateamos automáticamente al puerto local de tu túnel (56080)
                    clean_url = vnc_info.replace("controller:6080", "127.0.0.1:56080")
                    print(f"   🔗 Nueva URL: {clean_url}\n")
                else:
                    print("   ⚠️ No se pudo refrescar el token de video (API saturada).\n")
            else:
                print("   ❌ La máquina no está en condiciones de emitir video actualmente.\n")
        else:
            print(f"❌ {vm_name.upper()} ➔ Error de API (HTTP {r_stat.status_code})\n")

    except requests.exceptions.ConnectionError:
        print(f"❌ Error de Conexión: El puerto {NOVA_PORT} no responde. ¿Se cayó la VPN?\n")
        break

print("=========================================================")
