# Monitorización multicámara ("Batcomputer")

Sistema local que reúne las cámaras de un espacio (Dahua, Hikvision y Eufy) y
muestra en un mapa 2D cuántas personas hay en cada sala y cómo se mueven entre
salas. Es anónimo: no pone nombres ni guarda identidades.

- Qué falta y en qué estamos: [`ROADMAP.md`](ROADMAP.md)
- Conceptos clave, explicados: [`docs/guia.md`](docs/guia.md)
- Reglas de trabajo para agentes: [`AGENTS.md`](AGENTS.md)

## Arquitectura

```text
Dahua ──────► Colector Dahua ─────────────┐   (la IA la hace la cámara)
                                          ▼
Hikvision ┐                         idioma común
Eufy      ├─► Frigate ─► Adaptador ─► track_update.v1 ─► MQTT
          ┘   (IA en el PC)                               │
                                                          ▼
                                            Receptor (guarda todo)
                                                          ▼
                         Tracking engine: tracks → salas → ocupación → handoffs
                                                          ▼
                                                     Mapa 2D
Supervisor: arranca y vigila todas las piezas
```

## Dónde está cada pieza

| Pieza | Carpeta |
|---|---|
| Colector Dahua (C++ NetSDK + Python) | `experiments/dahua-netsdk/` |
| Frigate y Mosquitto en Docker (plantilla sin datos del sitio) | `deploy/docker/` |
| Inventario de cámaras (ejemplo) y lectura de modelo, firmware y hora | `deploy/camera-inventory.example.json`, `deploy/tools/` |
| Adaptador de Frigate | `experiments/frigate-adapter/` |
| Contratos de mensajes | `contracts/` |
| Transporte durable (outbox MQTT) | `services/track-transport/` |
| Receptor | `services/track-receiver/` |
| Tracking engine | `services/tracking-engine/` |
| Mapa, editor y monitor | `services/space-mapper/` |
| Supervisor del stack | `services/local-supervisor/` |
| Detector de Frigate en la GPU, fuera de Docker (en prueba) | `experiments/frigate-zmq-detector/` |
| Evidencia visual y benchmarks de visión | `experiments/visual-reid/` |
| Prueba de GPU en Windows | `experiments/windows-onnx-gpu/` |
| SDK oficial de Dahua (sin modificar) | `NetSDK/` |

## Arranque actual (desarrollo)

Frigate y Mosquitto se levantan antes, con Docker (después Docker Desktop los
vuelve a arrancar solo):

```powershell
docker compose -f deploy\docker\compose.yml up -d
python services\local-supervisor\run.py --check
python services\local-supervisor\run.py
```

El arranque único en el PC final es un objetivo pendiente (fase 4 del
roadmap).

## Tests

```powershell
experiments\visual-reid\.venv\Scripts\python.exe -m unittest discover -s tests\<área> -p "test_*.py"
```

Los datos del sitio (credenciales, IP, grabaciones, fotos y configuración
local) nunca se suben: el repositorio es público.
