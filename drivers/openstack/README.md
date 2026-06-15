# OpenStack Driver

Modulo reservado para la fase 2.

Este driver debera implementar el mismo contrato logico que `drivers/linux`, pero consumiendo APIs/SDK de OpenStack en lugar de ejecutar scripts por SSH.

Responsabilidades esperadas:

- Crear y destruir slices en OpenStack.
- Crear redes, subredes, routers o puertos segun el modelo definido.
- Crear VMs usando Nova.
- Usar o registrar imagenes en Glance.
- Consultar estado real de instancias, redes y consolas.
- Devolver inventario normalizado al Slice Manager.
