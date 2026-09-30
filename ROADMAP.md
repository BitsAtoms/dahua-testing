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
| Dahua | DH-IPC-HDBW7859Z-Z4-PV-X | 1 | Colector Dahua (NetSDK + CGI); es la cámara de pruebas 213 | La cámara |
| Dahua | DH-IPC-HDBW7859Z-Z4-PV-X-Black | 1 | Colector Dahua (mismo modelo que la 213, a confirmar por firmware) | La cámara |
| Dahua | DH-IPC-HDBW7459Z-Z-PV-X | 2 | Colector Dahua (capacidades a verificar) | La cámara |
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
- [x] **Ocupación por sala y total**, calculada con presencias y mostrada en
  el monitor (contador por sala y total). Es el núcleo del MVP. Validada en
  vivo con una persona el 2026-09-29; falta una prueba con varias personas.
- [~] **Capa de presencia v1** (`tracking_engine/presence.py`), común a todas
  las fuentes: unir por cercanía (0,15), confirmar a los 3 s y mantener 20 s.
  Con la sesión real de 26 minutos da el conteo correcto el 93,4 % del tiempo,
  frente al 78–83 % de contar tracks. Una persona puede
  generar varios tracks seguidos: la cámara los corta cada pocos minutos
  aunque la persona siga quieta en el mismo sitio. La presencia une los tracks
  del mismo lugar, exige unos segundos de track antes de contar y se mantiene
  unos segundos después del último. En una simulación con 26 minutos reales de
  la 212, el conteo correcto pasa del 83 % al 93 % del tiempo. Los parámetros
  se calibran con visitas en grupo después de colocar las cámaras (en grupos,
  unir por cercanía puede fusionar a dos personas que están muy juntas).
- [ ] Regla para cámaras que ven la misma sala, para no contar dos veces a
  la misma persona.
- [ ] Una cámara desconectada deja su sala en estado "desconocido", no a 0.
- [ ] **Ocupación v2: apariencia.** Evidencia actual (2026-09-29): en un paseo
  de la 212 a Reuniones, el sistema propuso los handoffs con buena coincidencia
  de tiempo (0,82–1,0), pero la cara no se pudo comparar nunca (Reuniones no
  obtuvo ninguna) y el cuerpo dio 0,02–0,44: no concluyente. Mejoras previstas:
  comparar presencias en vez de tracks (reuniendo los mejores recortes de
  varios tracks), buscar caras en las puertas al colocar las cámaras y calibrar
  con sesiones etiquetadas. Decisión del propietario: usar cara y cuerpo solo
  para no contar dos veces, nunca para identificar; revisión de privacidad
  antes de usarlo con visitantes.

Salida: en una prueba controlada con 1–2 personas y dos salas, los conteos
son correctos y la transición aparece en el mapa.

### Fase 2 — Posición en vivo de las Dahua `[~]`

El evento `HumanTrait` llega cuando la persona ya se fue, así que por sí solo
no sirve para el mapa en vivo.

- [x] Probar los datos IVS por fotograma del SDK (`PLAY_SetIVSCallBack`) con
  la Dahua 213 (2026-09-29). **Funciona:** la cámara envía unas 10 veces por
  segundo la posición de cada persona en formato ONVIF estándar. Los datos
  dejan de llegar en menos de un segundo cuando la persona sale, y cada
  visita nueva recibe un track nuevo. El número de track en vivo es el mismo
  `ObjectID` del `HumanTrait`, así que la posición en vivo y las fotos se
  unen directamente. Evidencia en `docs/dahua-research.md`.
- [x] Medir el coste de CPU (2026-09-29). Con el stream principal, unos 7 %
  de un núcleo y unos 440 MiB de memoria por cámara, sin usar la GPU. Para
  5 cámaras, un tercio de núcleo y unos 2,2 GiB: viable. El stream secundario
  solo entregó datos viejos al conectar, así que se usa el principal. El
  canal en vivo debe descartar datos con hora anterior a la conexión.
- Los planes B (metadatos ONVIF por RTSP, conteo por región o pasar las
  Dahua por Frigate) ya no hacen falta para la 213. Quedan de reserva por si
  otro modelo no ofrece el canal IVS.
- [~] Verificar los otros modelos. **HDBW7459Z-Z-PV-X: verificado** con la
  212 el 2026-09-29 (mismo firmware, mismo formato y mismos `ObjectID` que
  las fotos). Falta el HDBW5459Z-ZHE-PV-PRO, que no bloquea: se prueba
  cuando esté conectado, con el mismo programa. El sufijo `-Black` suele
  indicar solo el color de la carcasa.
- [~] **Hora de las cámaras.** La 212 tenía el reloj atrasado casi 27 días.
  El propietario activó NTP (`pool.ntp.org`) y ahora el desfase es de 0,67 s
  (2026-09-29). El sistema usa la hora del PC como referencia y avisará del
  desfase de cada cámara. Hay que revisar el NTP de cada cámara al
  instalarla.

Salida: una decisión documentada de cómo obtiene su posición en vivo cada
modelo Dahua. Ya está tomada para el HDBW7859Z-Z4-PV-X y el HDBW7459Z-Z-PV-X
(canal IVS por el stream principal); falta el HDBW5459Z-ZHE-PV-PRO, que no
bloquea.

### Fase 3 — Frigate para las cámaras no Dahua `[~]`

- [ ] Inventario de cámaras en un archivo de configuración, sin credenciales
  en Git.
- [x] Configuración de Frigate versionada como plantilla
  (`deploy/docker/frigate/config.template.yml`): go2rtc con una conexión por
  cámara, detección con CPU, clips de eventos de 7 días con el stream
  principal, y ejemplos de zonas y máscaras. Las URLs y contraseñas son
  variables `{FRIGATE_...}` guardadas en `deploy/docker/.env`, que Git ignora.
  Guardar una máscara desde la web mantiene las variables: no escribe las
  contraseñas. En uso en este PC desde el 2026-09-30.
- [x] Docker Compose de Frigate + Mosquitto dentro del repositorio
  (`deploy/docker/compose.yml`), con versiones fijas (Frigate 0.17.2,
  Mosquitto 2.1.2) y todos los puertos solo en `127.0.0.1`. Validado el
  2026-09-30 recreando los contenedores desde cero: las 3 cámaras reciben
  imagen, Frigate está conectado a MQTT, el modelo del robot se carga y solo
  escuchan puertos locales (antes 8971, 8554 y 8555 estaban abiertos a la red).
- [x] **Clips con el stream principal** en Recepción y Reuniones. Medido: la
  Hikvision DeepinView graba en 4K a unos 9 Mbps, unos 4 GB por hora con
  gente. Con 10 horas ocupadas al día son unos 290 GB por semana solo esa
  cámara; cabe en los 4 TB, pero hay que sumarlo con el inventario final.
- [x] **`puerta_planeta` solo para ver** (decisión del propietario,
  2026-09-30): sin detección ni clips. Se conecta directamente a la cámara,
  porque esa cámara manda los datos de arranque del vídeo (SPS/PPS) solo
  dentro del vídeo y go2rtc no los reenvía.
- [ ] Eufy S350: RTSP directo sin HomeBase y con el seguimiento automático y
  las patrullas desactivados (la imagen tiene que quedar fija).
- [ ] Hikvision DeepinView: evaluar sus eventos ISAPI como enriquecimiento
  opcional, después del MVP.
- [~] **Robot de Recepción, detección automática.** Validado en vivo el
  2026-09-29: el modelo `person_validity` de Frigate clasificó el robot
  (confianza 0,987), su track pasó a "excluido" y Recepción volvió a 0.
  El modelo queda activado de forma persistente: se cargó solo al recrear
  Frigate el 2026-09-30. Pendiente: vigilar el caso de un track que empieza
  en una persona y pasa al robot, y reentrenar con las posiciones definitivas
  de las cámaras. Frigate lo mantiene como
  "persona" hasta 64 minutos, y un track que empezó en una persona real se
  quedó pegado al robot 42 minutos. Como el robot cambia de sitio, una máscara
  no sirve. Solución: la clasificación de objetos de Frigate 0.17 (modelo
  sobre `person`, tipo *attribute*, clases `persona` y `robot`), entrenada en
  su web. El adaptador ya la traduce a "excluido" con
  `FRIGATE_TRACK_CLASSIFICATION_POLICY` y la ocupación ignora los excluidos.
  Hay que vigilar el caso de un track que empieza en una persona y pasa al
  robot.
- [ ] Detector de Frigate: pasar del detector de CPU básico (TFLite, que la
  documentación de Frigate no recomienda) a OpenVINO en CPU. El propietario
  quiere detectar mejor a las personas (2026-09-30), así que se comparan
  MobileNet (el modelo actual), YOLOv9 y D-FINE, todos en la CPU: consumo por
  cámara y personas perdidas o inventadas en las grabaciones de prueba. El
  modelo definitivo se elige después de colocar las cámaras. El reconocimiento
  facial y la búsqueda semántica no mejoran la detección: se ejecutan sobre
  personas ya detectadas.

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
| 2026-09-29 | Las Dahua dan su posición en vivo por el canal IVS (NetSDK + PlaySDK); `HumanTrait` queda como enriquecimiento con fotos, unido por `ObjectID` | Probado en la 213 con dos pruebas controladas; evita detectar en el PC para 5 de las 7 cámaras, pendiente de medir el coste de CPU |
| 2026-09-29 | Los clips de eventos de Frigate se graban con el stream principal | Decisión del propietario: más detalle al revisar. Medido en la Hikvision 4K: unos 4 GB por hora con gente (unos 290 GB por semana con 10 h diarias) |
| 2026-09-30 | `puerta_planeta` queda solo para ver, sin detección ni clips | Decisión del propietario: no se procesa, pero quiere verla en Frigate |
| 2026-09-29 | Frigate con versión fija y puertos solo en `127.0.0.1`; reconocimiento facial apagado en la plantilla | `stable` cambia solo con cada actualización; nada se expone a la red; la comparación facial espera la revisión de privacidad |

## Trabajo actual

Rama `codex/frigate-stack-template` (desde `main` en `7d86d3e`): fase 3.
La rama anterior, `codex/final-pc-readiness` (fase 2, ocupación v1 y robot
excluido), está mergeada en `main` local, que todavía no se subió a GitHub.

1. [x] **3.1 Plantilla** de Frigate y Mosquitto en `deploy/docker/`, sin
   datos del sitio, con tests que rechazan IPs, contraseñas, puertos abiertos
   a la red y versiones sin fijar.
2. [x] **3.2 El Frigate de este PC usa la plantilla** (2026-09-30). Las URLs
   están en `deploy/docker/.env`. Frigate sigue usando su carpeta
   (`frigate-runtime`, con el modelo del robot), y la cola de MQTT se copió al
   volumen nuevo. `puerta_planeta` queda solo para ver.
3. [ ] **3.3 Detector OpenVINO en CPU**, comparando MobileNet, YOLOv9 y
   D-FINE: consumo de CPU y personas perdidas o inventadas.
4. [ ] **3.4 Inventario** de las 7 cámaras en un archivo local, con un ejemplo
   versionado. Hay que aclarar qué cámara es `puerta_planeta`, que no figura en
   el inventario y ahora está en Frigate solo para ver.
5. [ ] **3.5 Eufy S350:** quitar el seguimiento automático y las patrullas.
6. [ ] Fase 4: un solo arranque.
7. [ ] Fase 5: instalación y guía para el PC final.

## Cómo retomar en una sesión nueva

1. Leer `AGENTS.md`, este roadmap y `git status`. `docs/guia.md` explica los
   conceptos, y `docs/dahua-research.md` la evidencia de las cámaras Dahua.
2. Qué hay en marcha en este PC de desarrollo:
   - Docker con Frigate 0.17.2 y Mosquitto, desde
     `docker compose -f deploy\docker\compose.yml up -d` (Docker Desktop los
     vuelve a arrancar solo). La configuración de Frigate sigue **fuera del
     repositorio**, en la carpeta `frigate-runtime/config` del propietario, y
     es un dato del sitio: no se sube. Los cambios en ella los hace el
     propietario desde la web de Frigate (*Configuration editor*). El Compose
     anterior quedó como `frigate-runtime/docker-compose.yml.old`, y la
     configuración anterior (con contraseñas) como
     `config.pre-template-20260930.yaml`: se puede borrar a partir del
     2026-10-07. El volumen viejo `frigate-adapter_frigate-mqtt-data` también
     se puede borrar entonces.
   - El supervisor: `python services\local-supervisor\run.py`.
3. Configuración local, que Git ignora:
   - `.env`: credenciales y `FRIGATE_TRACK_CLASSIFICATION_POLICY`;
   - `deploy/docker/.env`: carpetas de Frigate y URLs de sus cámaras;
   - `.env.dahua_212`: datos de la 212 para el programa de prueba;
   - `experiments/dahua-netsdk/cameras.local.json`: 213 y 212;
   - `runtime/space-mapper/space-map.json`: la 212 está en "Espacio 2", donde
     antes estaba la 213. Hay una copia del mapa anterior en
     `runtime/space-mapper/space-map.backup-20260929T152009.json`.
4. Estado de las cámaras de prueba: la 213 está desconectada y en su lugar
   está la 212. Cuando vuelva la 213, hay que añadirla otra vez al mapa. La
   212 tiene NTP (`pool.ntp.org`).
5. Pruebas contra el broker en marcha: usar siempre
   `TRACK_MQTT_TOPIC=tracking/test/track-updates` y un `TRACK_OUTBOX_ROOT`
   temporal, para no mezclar mensajes de prueba con el receptor.
6. Este PC no es el PC final (aquí hay una RTX 3050; el final tiene dos RX 9070
   XT). No se da por validado nada de GPU AMD desde aquí.

Siguiente paso: el 3.3 de "Trabajo actual", el detector OpenVINO en la CPU con
la comparación de modelos. Después, el inventario (3.4).

Pendiente del propietario: la revisión de privacidad antes de usar la
comparación facial (ocupación v2) con visitantes.
