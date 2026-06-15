# Image Manager

Modulo responsable del ciclo de vida de imagenes.

Estado actual:

- `runner/`: scripts auxiliares para subir imagenes a Drive con `rclone` y generar enlaces descargables.
- `uploads/`: staging local de archivos subidos antes de enviarlos a almacenamiento externo.

En fase 2 este modulo debera crecer para soportar tambien el flujo OpenStack/Glance.
