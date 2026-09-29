# Roadmap del sistema

Esta es la fuente de verdad del proyecto completo. La línea opcional de
visión propia con GPU (detector y tracker) tiene su detalle en
[`experiments/visual-reid/ROADMAP.md`](experiments/visual-reid/ROADMAP.md).
Los conceptos se explican en [`docs/guia.md`](docs/guia.md).

Estado: `[x]` hecho y validado · `[~]` implementado, falta validar ·
`[ ]` pendiente.

## Objetivo

Un PC Windows instalado en el sitio que, al encenderse, levanta solo todo el
sistema y muestra en su pantalla el mapa 2D "Batcomputer": cuántas personas
hay en cada sala, las transiciones entre salas y los eventos recientes con
fotos y clips. El sistema no pone nombres ni guarda identidades.

**Criterio de éxito del MVP** (del documento de arquitectura): mostrar en
tiempo real la ocupación estimada de cada sala y trasladar una presencia
entre dos salas adyacentes, sin depender de la marca de la cámara.

## Despliegue objetivo

**PC final:** Windows 11, AMD Ryzen 7 9850X3D (8 núcleos), 64 GB de RAM,
unos 4 TB de disco y dos AMD RX 9070 XT. Todavía no está en la red de las
cámaras.

**Cámaras:**

| Marca | Modelo | Cant. | Entrada al sistema | Quién detecta |
|---|---|---:|---|---|
| Dahua | DH-IPC-HDBW7459Z-Z-PV-X | 2 | Colector Dahua (NetSDK + CGI) | La cámara |
| Dahua | DH-IPC-HDBW7859Z-Z4-PV-X | 1 | Colector Dahua (capacidades a verificar) | La cámara |
| Dahua | DH-IPC-HDBW7859Z-Z4-PV-X-Black | 1 | Colector Dahua (capacidades a verificar) | La cámara |
| Dahua | DH-IPC-HDBW5459Z-ZHE-PV-PRO-Black | 1 | Colector Dahua (capacidades a verificar) | La cámara |
| Hikvision | iDS-2CD7186G0-IZS (DeepinView) | 1 | Frigate | El PC (CPU). La cámara tiene IA propia, aprovechable más adelante |
| Eufy | S350 | 1 | Frigate, RTSP directo sin HomeBase | El PC (CPU) |
| A definir | No Dahua | 1–2 | Frigate | El PC (CPU) |

En total son 7 cámaras y está previsto llegar a 9. Hay una segunda Hikvision
en el portero eléctrico, que no se incorpora. Las cámaras de prueba actuales
son la Dahua 213, la Hikvision de Recepción y la Eufy de Reuniones.

**Operación:**

- Un usuario de Windows dedicado con inicio de sesión automático.
- Un solo arranque: supervisor → Docker (Frigate y Mosquitto) → servicios
  propios → mapa en pantalla completa.
- El mapa solo se ve en la pantalla del PC final; nada se expone a la red.
- Retención de 7 días para todo. Frigate guarda solo clips de eventos.
- Frigate detecta con la CPU, porque Docker en Windows no puede usar las RX.
  Las GPU quedan para ReID y, solo si se mide que hace falta, para un
  detector propio.

## Qué depende de la posición final de las cámaras

Se construyen ahora todos los mecanismos y sus parámetros quedan como
configuración. Nada que se mida en la imagen (umbrales, máscaras, regiones,
tiempos de tránsito, elección de modelo) se calibra con las posiciones
provisionales. Las grabaciones actuales sirven como tests de regresión, no
para calibrar.

## Fases

### Fase 0 — Ingesta de fuentes `[x]`

- [x] Colector Dahua: NetSDK + CGI, fotos de cuerpo, cara y panorámica,
  correlación por `GroupID` y `RelativeID`/`BelongID`.
- [x] Adaptador de Frigate: ciclo `new`/`update`/`end`/`snapshot` y
  recortes de cara.
- [x] Contratos comunes `observation.v1` y `track_update.v1`.
- [x] Transporte que no pierde mensajes: outbox SQLite, MQTT QoS 1 y
  deduplicación en el receptor.
- [x] Retención de 7 días en cada módulo.

Evidencia: `docs/dahua-research.md`, validación en vivo de Frigate 0.17.2 el
2026-09-15 y 185 tests automáticos en verde.

### Fase 1 — Estado y mapa base `[~]`

- [x] Tracking engine: tracks locales reconstruibles desde el log del
  receptor.
- [x] Space Mapper: salas, cámaras y transiciones con tiempos de tránsito.
- [x] Candidatos de handoff por topología y tiempo, con la evidencia visual
  en un canal separado.
- [x] Monitor 2D en vivo y sesiones de validación etiquetadas.
- [ ] **Ocupación por sala y total**, calculada en el tracking engine y
  mostrada en el mapa. Es el núcleo del MVP.
- [ ] Regla para cámaras que ven la misma sala, para no contar dos veces a
  la misma persona.

Salida: en una prueba controlada con 1–2 personas y dos salas, los conteos
son correctos y la transición aparece en el mapa.

### Fase 2 — Posición en vivo de las Dahua `[ ]`

El evento `HumanTrait` llega cuando la persona ya se fue, así que por sí solo
no sirve para el mapa en vivo.

- [ ] Probar los datos IVS por fotograma del SDK (`PLAY_SetIVSCallBack`) con
  la Dahua 213.
- [ ] Si no funciona: probar un perfil ONVIF con metadatos y el conteo por
  región (`EVENT_IVS_MAN_NUM_DETECTION`).
- [ ] Si nada funciona: las Dahua también pasan por Frigate para la posición
  en vivo, y `HumanTrait` queda como enriquecimiento (caras, atributos,
  fotos).
- [ ] Verificar qué ofrecen los modelos HDBW7859Z-Z4-PV-X y
  HDBW5459Z-ZHE-PV-PRO (Video Metadata, `HumanTrait`, IVS), que aún no se
  han probado. El sufijo `-Black` suele indicar solo el color de la carcasa:
  basta probar una cámara por modelo, siempre que el firmware coincida.

Salida: una decisión documentada de cómo obtiene su posición en vivo cada
modelo Dahua.

### Fase 3 — Frigate para las cámaras no Dahua `[ ]`

- [ ] Inventario de cámaras en un archivo de configuración, sin credenciales
  en Git.
- [ ] Configuración de Frigate versionada como plantilla: go2rtc, detección
  con CPU, clips de eventos de 7 días, zonas y máscaras.
- [ ] Docker Compose de Frigate + Mosquitto dentro del repositorio.
- [ ] Eufy S350: RTSP directo sin HomeBase y con el seguimiento automático y
  las patrullas desactivados (la imagen tiene que quedar fija).
- [ ] Hikvision DeepinView: evaluar sus eventos ISAPI como enriquecimiento
  opcional, después del MVP.

Salida: Frigate se levanta desde el repositorio con las cámaras de prueba.

### Fase 4 — Un solo arranque y operación `[ ]`

- [ ] El supervisor levanta Docker además de los servicios propios, en orden,
  y espera a que cada pieza esté sana.
- [ ] Panel de salud: estado de cada cámara y servicio, y retraso del
  pipeline.
- [ ] Arranque automático: inicio de sesión automático, tarea al iniciar la
  sesión y navegador en modo kiosco.
- [ ] Recuperación ante cámara caída, reinicio de Docker y corte de luz.

Salida: después de reiniciar el PC, el sistema queda operativo sin que nadie
intervenga.

### Fase 5 — Preparación para el PC final `[ ]`

- [ ] Script de instalación reproducible (Python, entornos, Docker y
  colector Dahua) y un comando de verificación.
- [ ] Guía "cómo seguir en el PC final", incluida la creación manual de los
  secretos.
- [ ] Subir `main` a GitHub, revisando antes que no haya datos del sitio (el
  repositorio es público).

Salida: clonar, instalar y pasar la verificación en el PC final.

### Fase 6 — En el PC final, antes de colocar las cámaras `[ ]`

- [ ] Prueba de capacidad con 9 cámaras virtuales (las 7 previstas más 2
  futuras, con grabaciones re-emitidas por RTSP): CPU, RAM, disco y retrasos
  p50/p95/p99.
- [ ] Prueba prolongada de 24–72 h con cámaras virtuales.
- [ ] Si se usa la GPU: verificar que funciona desde el arranque automático.

### Fase 7 — Colocación de cámaras y aceptación `[ ]`

- [ ] Guía de colocación: altura, inclinación, puertas cubiertas y evitar
  pantallas y contraluz.
- [ ] Montaje de prueba y chequeo rápido de cada cámara.
- [ ] Kit de aceptación: escenarios guionados, grabación, conteo por
  segundo e informe automático.
- [ ] Calibración: máscaras, umbrales y tiempos de tránsito.

Salida: la ocupación alcanza el objetivo acordado, y los handoffs dudosos
quedan como pendientes en vez de convertirse en identidades erróneas.

### Fase 8 — Mejoras opcionales (solo con evidencia de que hacen falta)

- [ ] Detector y tracker propios con GPU (RF-DETR + DirectML): ver
  `experiments/visual-reid/ROADMAP.md`.
- [ ] Unir fragmentos de una persona dentro de la misma cámara, y ReID
  calibrado entre cámaras.
- [ ] Coordenadas en el suelo (homografía).

## Decisiones

| Fecha | Decisión | Motivo |
|---|---|---|
| 2026-09-28 | `main` consolida todo el trabajo previo (`e6f00f5`) | Había 29 commits en ramas sin integrar |
| 2026-09-28 | Frigate en Docker en el PC final para las cámaras no Dahua | Aporta conexión única por cámara, grabación, detección por movimiento, máscaras y eventos, y así no hay que reconstruirlo |
| 2026-09-28 | El detector propio con GPU pasa a ser opcional (fase 8) | Solo se retoma si Frigate no alcanza en calidad o en CPU |
| 2026-09-28 | Retención de 7 días; Frigate graba solo clips de eventos | Decisión del propietario |
| 2026-09-28 | Mapa solo en la pantalla local; usuario con inicio automático | Decisión del propietario |
| 2026-09-28 | El tracking se acepta por conteo correcto por sala y cero fusiones de identidad; la fragmentación se mide y se informa, pero no bloquea | Para contar, la fragmentación secuencial no cambia la ocupación; los errores que la rompen son los duplicados y las fusiones (ver `docs/guia.md`, sección 3) |

## Trabajo actual

Rama: `codex/final-pc-readiness`. Cubre, paso a paso, las fases 1 a 5 hasta
dejar todo listo para seguir en el PC final.

1. [x] Documentación al día: este roadmap, `AGENTS.md`, `README.md` y
   `docs/guia.md`.
2. [ ] Fase 2: prueba del canal IVS con la Dahua 213 (hacen falta unos 10
   minutos de prueba controlada del propietario).
3. [ ] Fase 1: ocupación por sala.
4. [ ] Fase 3: inventario y Frigate versionado.
5. [ ] Fase 4: un solo arranque.
6. [ ] Fase 5: instalación y guía para el PC final.
