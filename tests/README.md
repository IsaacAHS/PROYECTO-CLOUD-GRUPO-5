# Tests

Estructura prevista para pruebas de fase 2.

- `unit/`: validaciones puras de payloads, catalogos, placement y selector de drivers.
- `integration/`: pruebas entre Slice Manager, JSON store, cola de jobs y drivers en modo mock/dry-run.
- `e2e/`: flujos completos desde UI/API hasta despliegue simulado o real.
- `fixtures/`: payloads, inventarios y respuestas de SDK reutilizables.
