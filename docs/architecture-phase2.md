# Arquitectura Fase 2

Este documento registra la organizacion de carpetas para preparar NimbusCore para dos tipos de cluster: Linux/QEMU y OpenStack.

## Modulos Principales

```text
apps/
  web-ui/
    Interfaz web y presentacion.

  api-gateway/
    Nginx de entrada. Enruta UI, API y consola web.

  slice-manager/
    API FastAPI. Coordina plantillas, slices, cursos, jobs, inventario y seleccion de driver.

  console-access/
    noVNC/websockify. Acceso visual a consolas VNC.

drivers/
  linux/
    worker/
      Driver actual del cluster Linux. Consume jobs y ejecuta scripts por SSH.

    scripts/
      Scripts de infraestructura Linux: QEMU/KVM, OVS, TAPs, VLANs, cloud-init y redes.

  openstack/
    Futuro driver para OpenStack mediante SDK/API.

services/
  image-manager/
    runner/
      Scripts auxiliares para subir/publicar imagenes.

    uploads/
      Area temporal de imagenes subidas.

data/
  json-store/
    Persistencia JSON actual: imagenes, slices, templates, cursos y despliegues.

runtime/
  script-runs/
    Estado generado en ejecucion: inventario real, reservas VNC/VLAN/CIDR y resultados.

  jobs/
    Espacio reservado para jobs si se decide cambiar el volumen Docker por carpeta local.

  novnc-tokens/
    Espacio reservado para tokens si se decide cambiar el volumen Docker por carpeta local.

tests/
  unit/
  integration/
  e2e/
  fixtures/
```

## Criterio De Separacion

`slice-manager` debe ser el nucleo de orquestacion. No deberia depender directamente de detalles internos de QEMU, OVS o OpenStack. Esa responsabilidad debe vivir en drivers.

`drivers/linux` mantiene la implementacion existente para el cluster Linux.

`drivers/openstack` se agregara como segundo driver con el mismo contrato logico: crear slice, destruir slice, consultar estado, listar VMs y preparar consola.

`image-manager` debe concentrar el ciclo de vida de imagenes para que despues pueda soportar tanto QEMU/Linux como Glance/OpenStack.

`console-access` queda como modulo independiente porque no crea slices, pero si forma parte de la experiencia de acceso a VMs.
