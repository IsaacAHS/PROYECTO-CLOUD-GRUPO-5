# OpenStack Driver

Modulo reservado para la fase 2.

Este driver debera implementar el mismo contrato logico que `drivers/linux`, pero consumiendo APIs/SDK de OpenStack en lugar de ejecutar scripts por SSH.

El selector de drivers vive en `apps/slice-manager/app/drivers/selector.py`.
El contrato comun esta en `apps/slice-manager/app/drivers/base.py`.
Para habilitar OpenStack se debe reemplazar el placeholder
`OpenStackClusterDriver` por una implementacion que construya y ejecute el
despliegue usando el SDK.

Responsabilidades esperadas:

- Crear y destruir slices en OpenStack.
- Crear redes, subredes, routers o puertos segun el modelo definido.
- Crear VMs usando Nova.
- Usar o registrar imagenes en Glance.
- Consultar estado real de instancias, redes y consolas.
- Devolver inventario normalizado al Slice Manager.
