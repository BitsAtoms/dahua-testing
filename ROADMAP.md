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
en el portero eléctrico, un terminal de control de acceso DS-K1T502DBFWX-C
(`puerta_planeta`), que no se procesa: solo se ve en Frigate. Las cámaras de
prueba actuales son las Dahua 212 y 213, la Hikvision de Recepción y la Eufy
de Reuniones. El detalle de cada una (modelo, firmware y cómo entra al
sistema) está en el inventario local `deploy/camera-inventory.local.json`.

**Operación:**

- Un usuario de Windows dedicado con inicio de sesión automático.
- Un solo arranque: supervisor → Docker (Frigate y Mosquitto) → servicios
  propios → mapa en pantalla completa.
- El mapa solo se ve en la pantalla del PC final; nada se expone a la red.
- Retención de 7 días para todo. Frigate guarda solo clips de eventos.
- Frigate corre en Docker, que en Windows no puede usar las RX: decodifica
  el vídeo en la CPU. Su detector puede pasar a una RX mediante el detector
  externo de Frigate (tipo `zmq`), con un programa nativo de Windows; está en
  prueba y OpenVINO en la CPU queda como respaldo. La otra GPU queda para
  ReID.

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
  instalarla: `deploy/tools/camera_info.py` muestra el desfase de las Dahua y
  las Hikvision (2026-09-30: 212 y Recepción 0 s, terminal de la puerta
  −3 s).

Salida: una decisión documentada de cómo obtiene su posición en vivo cada
modelo Dahua. Ya está tomada para el HDBW7859Z-Z4-PV-X y el HDBW7459Z-Z-PV-X
(canal IVS por el stream principal); falta el HDBW5459Z-ZHE-PV-PRO, que no
bloquea.

### Fase 3 — Frigate para las cámaras no Dahua `[x]`

- [x] Inventario de cámaras en un archivo de configuración, sin credenciales
  en Git (2026-09-30). El ejemplo versionado es
  `deploy/camera-inventory.example.json` y el real,
  `deploy/camera-inventory.local.json`, que Git ignora. Cada cámara tiene
  modelo, firmware, cómo entra al sistema, si su posición en vivo está
  verificada y su estado (prevista, de prueba o instalada). La sala queda
  vacía hasta la colocación definitiva. `deploy/tools/camera_info.py` lee el
  modelo, el firmware y la hora de las Dahua y las Hikvision conectadas; la
  Eufy no tiene interfaz para eso.
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
- [x] Eufy S350: RTSP directo sin HomeBase y con el seguimiento automático y
  las patrullas desactivados (la imagen tiene que quedar fija). Confirmado
  por el propietario el 2026-09-30.
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
- [x] Detector de Frigate. El propietario quiere detectar mejor a las
  personas y aprovechar las GPU (2026-09-30). Dos vías, en este orden:
  1. **Respaldo en la CPU:** pasar del detector de CPU básico (TFLite, que la
     documentación de Frigate no recomienda) a OpenVINO en la CPU. Hecho el
     2026-09-30. Medido en este PC (i9-12900F) con la misma carga, unas 13
     detecciones por segundo: 6,1 ms por imagen frente a 11,3 ms, pero 1 núcleo
     de CPU frente a 0,4. Frigate configura OpenVINO para responder rápido y
     no deja ajustarlo. Se mantiene porque es el motor que Frigate recomienda
     para los modelos grandes en la CPU. Hay que repetir la medición en el
     Ryzen del PC final.
  2. **GPU:** Frigate 0.17 puede enviar las imágenes a un detector externo
     (tipo `zmq`), un programa fuera de Docker. El equipo de Frigate publica
     uno genérico con ONNX Runtime (`frigate-nvr/apple-silicon-detector`,
     MIT), hecho para Mac y no documentado para Windows. En Windows puede usar
     DirectML o MIGraphX (AMD); su preparación está en
     `experiments/windows-onnx-gpu`. **Mecanismo validado en este PC el
     2026-09-30** (`experiments/frigate-zmq-detector`), con RF-DETR Medium a
     320×320 en la RTX 3050 (DirectML, sin ninguna operación en la CPU):
     37 ms por imagen vistos por Frigate (13 ms de modelo, 8 ms de viaje
     desde Docker), y detectar pasa a costar un 6 % de un núcleo frente al
     41–104 % de antes. Frigate registró personas con confianza 0,79–0,99. El
     rendimiento con las RX se mide en el PC final (fase 6).

  Hallazgos que condicionan la elección del modelo:
  - Frigate no aplica la normalización de color que espera RF-DETR, y puntúa
    sus detecciones de otra forma que el banco de pruebas de
    `experiments/visual-reid`. Por eso la exportación para Frigate lleva la
    normalización dentro, y la calidad se mide a través del propio Frigate,
    re-emitiendo las grabaciones de prueba como cámaras virtuales.
  - YOLOv9 tiene licencia GPL-3.0; RF-DETR y D-FINE, Apache-2.0.

  **Comparación a través de Frigate** (2026-09-30,
  `experiments/frigate-replay-bench`): las 7 grabaciones de prueba entran en
  bucle en un Frigate aislado, con cada modelo a 320×320.
  - MobileNet (el actual) no inventa personas, pero en las poses variadas solo
    ve a la persona un 33 % del tiempo, y en los cruces cuenta una tercera
    persona.
  - **RF-DETR M y YOLOv9 M**: cero falsos positivos con el robot y con las
    pantallas, cobertura completa y el mejor cruce en Recepción (nunca más de 2
    personas). RF-DETR fragmenta algo menos.
  - D-FINE M queda descartado: toma al robot por una persona todo el tiempo y
    en DirectML da resultados incorrectos.
  - Con 3–4 cámaras a la vez, un solo detector en la RTX 3050 no dio abasto
    (se saltó hasta un 34 % de las imágenes), lo que infla la fragmentación.

  Recomendación: RF-DETR M como modelo principal en la GPU, y YOLOv9 M como
  alternativa; los dos quedan exportados. Son aproximaciones sin anotación
  segundo a segundo y con las cámaras en posiciones provisionales: el modelo
  definitivo se elige en el PC final, después de colocar las cámaras. El
  reconocimiento facial y la búsqueda semántica no mejoran la detección: se
  ejecutan sobre personas ya detectadas.
- Quedan para más adelante: el enriquecimiento con ISAPI de la Hikvision
  (opcional, después del MVP) y reentrenar el modelo del robot al colocar las
  cámaras (fase 7).

Salida: Frigate se levanta desde el repositorio con las cámaras de prueba.

### Fase 4 — Un solo arranque y operación `[ ]`

- [x] El supervisor levanta Docker además de los servicios propios, en orden,
  y espera a que cada pieza esté sana. Si Frigate usa el detector con GPU, el
  programa detector arranca **antes** que Frigate, y si ese programa se
  reinicia, hay que reiniciar Frigate después. Probado el 2026-09-30 en los
  dos sentidos:
  - si el programa se cae con Frigate en marcha, las cámaras quedan atascadas;
  - si Frigate arranca sin el programa, queda **ciego sin que se note**: las
    cámaras parecen sanas, pero el detector devuelve siempre "cero personas"
    (`Model not ready`), porque solo comprueba el modelo al arrancar. Cuando
    el programa vuelve, Frigate no se recupera solo.

  En los dos casos basta con reiniciar Frigate con el programa ya en marcha:
  en 13 s vuelve a detectar. Como Docker arranca Frigate en cuanto se
  enciende, el supervisor lo reinicia una vez cuando el programa está listo.

  **Hecho y validado en este PC el 2026-09-30** (4.1). El orden es: Docker
  Desktop (lo arranca si está apagado) → Mosquitto → programa detector →
  Frigate → servicios propios. El ajuste `frigate_detector` (`cpu` o `gpu`)
  de la configuración local del supervisor dice si hay programa detector, y
  el supervisor avisa si Frigate usa otro detector. Probado en vivo:
  - con Docker Desktop apagado y Frigate encendido antes, todo quedó en
    marcha en unos 25 s y Frigate detectaba a 21 ms por imagen;
  - al matar el programa detector, el supervisor lo volvió a arrancar en 1 s
    y reinició Frigate, que se recuperó en un minuto;
  - al parar el supervisor se paran los servicios, Frigate y el programa;
    Mosquitto y Docker Desktop siguen encendidos (decisión del propietario).

  Detalle en `services/local-supervisor/README.md`.
- [ ] Panel de salud: estado de cada cámara y servicio, y retraso del
  pipeline. Para Frigate, tres señales del detector:
  - **atascado:** `process_fps` muy por debajo de `camera_fps`, con
    `skipped_fps` cerca de los fps de la cámara;
  - **ciego:** tiempo por imagen por debajo de 1 ms, o `Model not ready` en el
    log, aunque las cámaras parezcan sanas;
  - **roto:** tiempo por imagen absurdo o congelado.
- [x] Arranque automático: inicio de sesión automático, tarea al iniciar la
  sesión y navegador en modo kiosco. **Validado en este PC el 2026-10-01**
  reiniciando de verdad: del encendido al mapa en pantalla, 85 s; desde el
  inicio de sesión, 56 s, sin tocar nada. Ese día:
  - `deploy/windows/install-autostart.ps1` registró, sin permisos de
    administrador, la tarea "Batcomputer" del Programador de tareas;
  - la tarea lanza `services/local-supervisor/autostart.py`, que reintenta el
    supervisor si falla al arrancar y abre el mapa en una ventana de Chrome a
    pantalla completa;
  - `F11` alterna entre pantalla completa y ventana normal, `Ctrl+Alt+B`
    vuelve a abrir el mapa y `Alt+F4` lo cierra (decisión del propietario:
    poder trabajar en este PC);
  - el supervisor impide la suspensión mientras está en marcha y guarda su
    salida en `supervisor.log`.

  En este PC el inicio de sesión fue manual. El PC es compartido y su inicio
  automático pertenece a otra cuenta, que no se tocó (decisión del
  propietario). El PC final tiene una sola cuenta, que entra sola.
- [ ] Recuperación ante cámara caída, reinicio de Docker y corte de luz.

Salida: después de reiniciar el PC, el sistema queda operativo sin que nadie
intervenga.

### Presentación en las 9 pantallas del Batcomputer `[~]`

El PC final está en una réplica del ordenador de Batman con 9 pantallas: dos
verticales a los lados (`side_left`, `side_right`), un bloque central de 2×2
(`top_left`, `top_right`, `bottom_left`, `bottom_right`) y tres pequeñas
empotradas en la consola (`mini_left`, `mini_center`, `mini_right`). Cada
pantalla tendrá su propia ventana, que al arrancar se coloca sola en su sitio.
Este trabajo empezó el 2026-10-01, antes de la fase 5, porque la instalación
tiene que colocar esas ventanas. La parte visual del panel de salud (4.2) se
hace aquí. El diseño completo, pendiente de aprobar, está en
[`docs/diseno-batcomputer.md`](docs/diseno-batcomputer.md); el estilo, en
`services/batcomputer-ui/README.md`.

- [x] Estilo: la paleta del propietario y una muestra de las 9 pantallas a
  escala (2026-10-01). Predominan el negro y el negro lavado, con el amarillo
  como contraste; el rojo queda solo para lo crítico. Ningún estado se
  distingue solo por el color.
- [x] Contenido de las pantallas pequeñas: salud de las partes (`mini_left`),
  consola del supervisor (`mini_center`) y salud de las cámaras
  (`mini_right`). Solo muestran información: no son interactivas y el ratón
  no debe llegar a ellas.
- [x] Inventario de las pantallas del PC final (2026-10-01): seis monitores
  iguales de 32" 4K (las laterales giradas) y tres pequeñas de 1080p por HDMI.
  Cada RX lleva una columna y la gráfica integrada, `mini_center`. La
  principal, `bottom_left`, está al 150 %. Todas tienen casi la misma densidad
  de píxeles, así que todas las ventanas se dibujan con una escala fija ×2. El
  inventario completo está en `services/batcomputer-ui/displays.local.json`,
  que Git ignora.
- [~] Contenido de las pantallas grandes (2026-10-01):
  - `top_left`: videovigilancia con todas las cámaras;
  - `top_right`: mapa de seguimiento;
  - `side_left`: eventos de Dahua con miniaturas y el registro del colector;
  - `bottom_left` y `bottom_right`: libres, para trabajar.

  - `side_right` (aprobado el mismo día): recorridos entre salas y resumen
    del día;
  - `side_left` reúne los eventos de todas las fuentes, Dahua y Frigate.

  La idea es que cada pantalla muestre una parte distinta del sistema.
- [ ] Censura de la videovigilancia: `Ctrl+Alt+X` (una tecla de la Stream
  Deck) alterna entre las imágenes y estática.
- [~] Editor de espacios nuevo, en una pantalla de trabajo, para crear las
  salas, colocar las cámaras y unir las salas. El actual no convence al
  propietario y arrastra herramientas de pruebas anteriores. Se organiza en espacios de
  trabajo (un edificio), con hasta 3 plantas, salas dibujadas sobre una
  rejilla de ayuda, puertas, Exterior y conexiones entre plantas. Los tiempos
  de paso no se escriben a mano: la propuesta es medirlos por puerta, según por
  dónde sale y entra cada persona en la imagen de cada cámara. Se hace en
  cuatro partes (2026-10-05):
  - [x] **2a. El plano** (`space_map.v2`): el archivo que describe el
    edificio, con validación, copia de cada versión anterior y aviso si otra
    ventana lo cambió. Las paredes van solo en horizontal o en vertical, así
    que cada sala ocupa casillas enteras de la rejilla y los solapes, las
    paredes compartidas y las puertas se comprueban sin aproximaciones. Se
    guarda aparte, en `runtime/spaces/space-map.json`: hasta la parte 2d nada
    más cambia.
  - [~] **2b. El editor**, en `http://127.0.0.1:8092/editor`. Las 3 salas de
    prueba se dibujan de nuevo en él, sin importar el mapa actual (decisión
    del propietario). En dos vueltas:
    - [x] **2b-1, plantas y salas**, aprobada por el propietario el
      2026-10-05, que ya dibujó con ella las salas de prueba. Permite crear y renombrar hasta 3 plantas, dibujar
      rectángulos arrastrando o formas en L esquina a esquina, mover
      esquinas, paredes y salas enteras, deshacer y guardar. Probado en Chrome
      con clics y arrastres simulados: salas válidas, solapes rechazados con
      su motivo, deshacer, borrar planta en dos pasos y aviso si otra ventana
      guardó antes.
    - [~] **2b-2, cámaras, puertas, Exterior, escaleras y avisos**
      (2026-10-05), pendiente de la revisión del propietario. Las cámaras
      se arrastran desde una bandeja con todas las que conoce el sistema.
      Las puertas se dibujan a lo largo de una pared: entre dos salas, o
      como entrada desde el Exterior. Al mover o cambiar una sala, sus
      cámaras y entradas la acompañan y las puertas que ya no están en su
      pared se quitan con un aviso, así que lo que se guarda siempre es
      válido. Probado en Chrome y con un test que comprueba que el servidor
      acepta cada plano que deja el editor. El propietario ya colocó en el
      plano nuevo sus 3 cámaras y las puertas.
    - [~] **Vista adicional** (propuesta del propietario, 2026-10-05): en
      cada cámara se marcan las otras salas que también salen en parte de su
      imagen, como la Recepción que ve en parte la `dahua_212`. Sirve para que
      el seguimiento acepte que dos cámaras vean a la vez a la misma persona.
      No decide en qué sala está cada persona: eso lo harán las zonas de la
      imagen (punto 8 del diseño), que se calibran con las cámaras en su sitio
      definitivo (fase 7).
  - [x] **2c. El mapa en vivo** de `top_right`, aprobado por el propietario
    el 2026-10-06. Muestra las plantas una al lado de otra, el
    número de personas de cada sala y un punto por persona: relleno si una
    cámara la ve ahora, hueco si ya no se ve pero aún se cuenta (decisión del
    propietario). Usa ya el plano nuevo para saber en qué sala cuenta cada
    cámara. Cuando el sistema cree que alguien pasó de una sala a otra, su
    punto cruza la puerta y la sala de origen deja de contarlo en ese momento
    (**traspaso**, decisión del propietario; la regla está en el seguimiento).
    Reproduciendo 24 h reales: 47 pasos, y un 2,6 % menos de personas
    contadas en dos salas a la vez. Aspecto de sonar (propietario,
    2026-10-06): cada punto relleno late por su cuenta cada 2 a 3 s, con un
    pequeño salto al azar y un anillo, y se apaga casi del todo antes del
    siguiente latido; es simulado, la cámara solo decide si el punto existe.
    Los huecos quedan quietos y tenues, y un barrido suave recorre el mapa.
    `?demo` inventa personas para revisar el aspecto.
  - [ ] **2d. El cambio:** el seguimiento y las pantallas pasan al plano
    nuevo, con un margen general de hasta 30 s por puerta hasta la fase 7, y
    se retiran el editor y el monitor antiguos (puerto 8091).
- [ ] Ratón encerrado en las dos pantallas de trabajo, por software, con
  atajos para soltarlo y para centrarlo.
- [x] Documento de diseño aprobado por el propietario el 2026-10-01. Se
  construye **pantalla a pantalla**: cada una se revisa y se ajusta en este PC
  antes de pasar a la siguiente, y el arranque de las 9 ventanas en su sitio
  queda para el final.
- [x] **Base** (2026-10-01): el servicio `services/batcomputer-ui` (puerto
  8092, ya incluido en el supervisor) sirve una página por pantalla y una
  vista previa de las 9 a escala con datos reales
  (`http://127.0.0.1:8092/preview`). El bloque amarillo de las cabeceras es
  decorativo, con un icono y sin letras.
- [x] **`mini_center`, consola del supervisor** (aprobada el 2026-10-01).
  Cuenta en palabras sencillas lo que pasa de verdad: "Recepción · nueva
  persona a la vista (#4509)". Se descarta el ruido interno: 4000 líneas
  técnicas en 12 minutos se quedaron en 41 frases.
- [x] **`mini_left`, salud de las partes** (aprobada el 2026-10-01). Seis
  filas reales: contenedores, mensajería, Frigate, el detector con las alarmas
  de ciego, atascado, congelado y roto, los servicios y el retraso. Además:
  - una fila **GOTHAM** con avisos inventados del universo de Batman, que no
    cuentan como alarmas;
  - con un 1 % de probabilidad, un acertijo de **Enigma** en verde.

  Es la parte de datos del paso 4.2.
- [x] **`mini_right`, salud de las cámaras** (aprobada el 2026-10-01): si cada
  cámara tiene conexión y cuándo vio a alguien por última vez ("viendo
  personas").
- [ ] Siguientes: el editor de espacios con el mapa en vivo, la
  videovigilancia, los eventos y los recorridos. Al final, las ventanas
  colocadas en sus pantallas al arrancar y el ratón encerrado.

### Fase 5 — Preparación para el PC final `[ ]`

- [ ] Script de instalación reproducible (Python, entornos, Docker y
  colector Dahua) y un comando de verificación.
- [ ] Guía "cómo seguir en el PC final", incluida la creación manual de los
  secretos.
- [~] Subir `main` a GitHub, revisando antes que no haya datos del sitio (el
  repositorio es público). Hecho el 2026-09-30 hasta `d95be09` y el
  2026-10-05 hasta `f67fa6f`; hay que repetir la revisión antes de cada
  subida.

Salida: clonar, instalar y pasar la verificación en el PC final.

### Fase 6 — En el PC final, antes de colocar las cámaras `[ ]`

- [ ] Prueba de capacidad con 9 cámaras virtuales (las 7 previstas más 2
  futuras, con grabaciones re-emitidas por RTSP): CPU, RAM, disco y retrasos
  p50/p95/p99.
- [ ] Prueba prolongada de 24–72 h con cámaras virtuales.
- [ ] Detector de Frigate en una RX 9070 XT (detector externo `zmq` con
  MIGraphX o DirectML): tiempo por imagen, CPU liberada y comportamiento si el
  programa externo se cae (respaldo en la CPU). Medir cuántas cámaras aguanta
  un detector y probar uno por GPU (dos programas y dos detectores `zmq`),
  porque en la RTX 3050 uno solo no dio abasto con 4 cámaras. Probar D-FINE
  con MIGraphX. El banco `experiments/frigate-replay-bench` sirve de base.
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
| 2026-09-30 | Detector de Frigate: OpenVINO en la CPU como respaldo, y prueba de la GPU con el detector externo de Frigate (`zmq`) y un programa nativo de Windows | Decisión del propietario: aprovechar las RX 9070 XT sin sacar Frigate de Docker. Docker en Windows no da acceso a las GPU AMD; la versión ROCm de Frigate necesita Linux |
| 2026-09-30 | Modelo recomendado para el detector de Frigate en la GPU: RF-DETR M, con YOLOv9 M como alternativa; D-FINE M descartado | Comparación a través de Frigate con las grabaciones de prueba; la elección final se hace en el PC final tras colocar las cámaras |
| 2026-09-30 | Las licencias de los modelos (por ejemplo, GPL-3.0 de YOLOv9) no limitan la elección: se busca la mejor detección | Decisión del propietario: el sistema es una demo interna de capacidades y no se comercializa |
| 2026-09-29 | Frigate con versión fija y puertos solo en `127.0.0.1`; reconocimiento facial apagado en la plantilla | `stable` cambia solo con cada actualización; nada se expone a la red; la comparación facial espera la revisión de privacidad |
| 2026-09-30 | Fase 4: el supervisor arranca también Docker Desktop, y este PC de desarrollo vuelve al detector con GPU | Decisión del propietario: el supervisor controla el orden completo, y probar con la GPU da más indicios de que funcionará en el PC final |
| 2026-09-30 | El arranque completo (inicio de sesión automático, tarea al iniciar la sesión y modo kiosco) se prueba primero en este PC | Decisión del propietario: al encender el PC no hay que hacer nada más; el PC final usará el mismo inicio de sesión automático de Windows |
| 2026-09-30 | Al parar el supervisor se paran también Frigate y el programa detector; Mosquitto y Docker Desktop siguen encendidos | Decisión del propietario: sin el programa detector, Frigate quedaría ciego |
| 2026-10-01 | En este PC compartido no se toca el inicio de sesión automático (es de otra cuenta); el propietario entra a mano | Decisión del propietario: el PC final tiene una sola cuenta, que entra sola |
| 2026-10-01 | El supervisor impide la suspensión mientras está en marcha, sin cambiar los ajustes de energía | Decisión del propietario; el PC final ya estará configurado para no apagarse |
| 2026-10-01 | El mapa se muestra en una ventana de aplicación de Chrome a pantalla completa, que `F11` alterna, en vez del modo `--kiosk` | Decisión del propietario: poder ver el modo kiosco y seguir trabajando en este PC; si el PC final necesita el kiosco bloqueado se decide en la fase 5 |
| 2026-10-01 | Estilo de las pantallas: Batcomputer moderno con toques del Batman clásico y de los 90. Paleta amarillo `#FDE311`, ocre `#988829`, azul grisáceo oscuro `#282e3c` y claro `#505c7c`, negro lavado `#242424` y negro `#020202`; predominan los negros | Decisión del propietario |
| 2026-10-01 | Rojo `#FF4D3A` solo para alarmas y situaciones críticas o especiales; sin logotipos de Batman por ahora (un detector de batiseñal queda como idea para más adelante) | Decisión del propietario |
| 2026-10-01 | Pantallas pequeñas: salud de las partes, consola del supervisor y salud de las cámaras, sin interacción ni ratón | Decisión del propietario |
| 2026-10-01 | `bottom_left` y `bottom_right` quedan libres para trabajar; `top_left` muestra la videovigilancia, `top_right` el mapa y `side_left` los eventos de Dahua con miniaturas y registro | Decisión del propietario |
| 2026-10-01 | El ratón se encierra por software en las dos pantallas de trabajo, con atajos para soltarlo y centrarlo | Decisión del propietario: no es fácil alinear las pantallas en Windows sin ver dónde quedan |
| 2026-10-01 | `side_right`: recorridos y resumen del día; `side_left`: eventos de todas las fuentes; `Ctrl+Alt+X` (Stream Deck) censura los streams con estática | Decisión del propietario: cada pantalla muestra una parte del sistema |
| 2026-10-05 | El editor nuevo guarda el plano en un archivo aparte hasta que el editor y el mapa estén aprobados; las 3 salas de prueba se dibujan de nuevo, sin importar el mapa actual | Decisión del propietario: nada deja de funcionar mientras se construye |
| 2026-10-05 | Traspaso: cuando el sistema cree que una persona pasó de una sala a otra, la sala de origen deja de contarla en cuanto cuenta en la nueva, en vez de mantenerla 20 s; en el mapa, punto hueco para quien no se ve pero se sigue contando | Decisión del propietario: la misma persona no aparece en dos salas. Si la hipótesis falla, la sala de origen cuenta una persona menos hasta que la cámara la vuelve a ver |
| 2026-10-06 | Mapa en vivo con aspecto de sonar: cada punto visto late por su cuenta cada 2-3 s con un pequeño salto al azar y se apaga casi del todo entre latidos; los huecos no laten; se mantiene el barrido | Decisión del propietario: los puntos estáticos parecían muertos. El salto es decorativo, no una posición |
| 2026-10-05 | Cada cámara puede marcar una "vista adicional" de otras salas; las zonas de cada sala en la imagen se dibujan después, con las cámaras en su sitio definitivo | Propuesta del propietario. La lista sirve ya para los pasos solapados entre cámaras; contar a cada persona en su sala exige zonas de la imagen, que dependen de la posición final |

## Trabajo actual

Fase 3 cerrada el 2026-09-30: la rama `codex/frigate-stack-template` está
mergeada en `main` (`d95be09`), y `main` está subido a GitHub después de
revisar todos los parches (sin IP, contraseñas, grabaciones ni configuración
local). La fase 4 está en marcha. Su primera rama, `codex/single-startup`
(4.0, 4.1 y 4.3: el arranque único), está mergeada en `main` (`1af2aaa`) el
2026-10-01 y subida a GitHub el 2026-10-05. El panel de salud (4.2) sigue en la
rama `codex/health-panel`.

1. [x] **3.1 Plantilla** de Frigate y Mosquitto en `deploy/docker/`, sin
   datos del sitio, con tests que rechazan IPs, contraseñas, puertos abiertos
   a la red y versiones sin fijar.
2. [x] **3.2 El Frigate de este PC usa la plantilla** (2026-09-30). Las URLs
   están en `deploy/docker/.env`. Frigate sigue usando su carpeta
   (`frigate-runtime`, con el modelo del robot), y la cola de MQTT se copió al
   volumen nuevo. `puerta_planeta` queda solo para ver.
3. [x] **3.3a Detector OpenVINO en la CPU** (respaldo), activo en este PC
   desde el 2026-09-30: responde en la mitad de tiempo, pero gasta unas 2,5
   veces más CPU que TFLite con la misma carga.
4. [x] **3.3b Detector externo con GPU** en este PC (RTX 3050), validado el
   2026-09-30 con RF-DETR Medium: Frigate detecta a través del programa
   nativo de Windows, y detectar pasa a costar un 6 % de un núcleo. Probada
   también la caída del programa: exige reiniciar Frigate. Hasta la fase 4,
   este PC vuelve a OpenVINO, porque el programa todavía no arranca solo.
5. [x] **3.3c Comparar modelos a través de Frigate** (2026-09-30), con las
   grabaciones de prueba como cámaras virtuales: RF-DETR M y YOLOv9 M superan
   claramente a MobileNet; D-FINE M queda descartado.
6. [x] **3.4 Inventario** de las cámaras en un archivo local, con un ejemplo
   versionado (2026-09-30). `puerta_planeta` resultó ser el terminal de
   control de acceso de la puerta. Las salas se asignan al colocar las
   cámaras.
7. [x] **3.5 Eufy S350:** sin seguimiento automático ni patrullas (confirmado
   por el propietario, 2026-09-30).
8. [ ] Fase 4: un solo arranque.
   - [x] **4.0 Frigate sin su programa detector** (2026-09-30): queda ciego
     y no se recupera solo; reiniciarlo con el programa en marcha lo arregla
     en 13 s.
   - [x] **4.1** El supervisor arranca Docker Desktop, Mosquitto, el programa
     detector y Frigate, en ese orden y esperando a cada uno (2026-09-30).
     Reinicia Frigate si arrancó antes que el programa o si el programa se
     reinicia. Validado con un arranque en frío de Docker Desktop, una caída
     del programa y una parada limpia.
   - [ ] **4.2** Panel de salud.
   - [x] **4.3** Tarea al iniciar la sesión y mapa a pantalla completa en
     Chrome, probados con un reinicio real de este PC (2026-10-01): mapa en
     pantalla 85 s después del encendido.
   - [ ] **4.4** Recuperación ante cámara caída, reinicio de Docker y corte de
     luz.
9. [ ] Fase 5: instalación y guía para el PC final.

## Cómo retomar en una sesión nueva

1. Leer `AGENTS.md`, este roadmap y `git status`. `docs/guia.md` explica los
   conceptos, y `docs/dahua-research.md` la evidencia de las cámaras Dahua.
2. Qué hay en marcha en este PC de desarrollo:
   - Docker con Frigate 0.17.2 y Mosquitto, definidos en
     `deploy\docker\compose.yml` y arrancados por el supervisor. Docker
     Desktop no arranca solo al iniciar sesión en este PC; lo arranca el
     supervisor. La configuración de Frigate sigue **fuera del
     repositorio**, en la carpeta `frigate-runtime/config` del propietario, y
     es un dato del sitio: no se sube. Los cambios en ella los hace el
     propietario desde la web de Frigate (*Configuration editor*). El Compose
     anterior quedó como `frigate-runtime/docker-compose.yml.old`, y la
     configuración anterior (con contraseñas) como
     `config.pre-template-20260930.yaml`: se puede borrar a partir del
     2026-10-07. El volumen viejo `frigate-adapter_frigate-mqtt-data` también
     se puede borrar entonces.
   - El detector de Frigate en este PC vuelve a ser el de la GPU desde el
     2026-09-30 (RF-DETR Medium, detector `zmq`, decisión del propietario para
     la fase 4). Por eso `services/local-supervisor/local-stack.json` (que Git
     ignora) tiene `"frigate_detector": "gpu"`. Para volver a OpenVINO, se
     restauran los bloques `detectors`/`model` de
     `deploy/docker/frigate/config.template.yml` en el *Configuration editor*
     y se pone `"cpu"` en ese archivo.
   - El supervisor lo arranca todo. Desde el 2026-10-01 lo lanza la tarea
     "Batcomputer" al iniciar la sesión de `Agustin`, con la ventana del mapa.
     Se quita con `deploy\windows\install-autostart.ps1 -Uninstall`. A mano:
     `python services\local-supervisor\run.py`. Sin el supervisor, Frigate
     queda parado o ciego.
3. Configuración local, que Git ignora:
   - `.env`: credenciales y `FRIGATE_TRACK_CLASSIFICATION_POLICY`;
   - `deploy/docker/.env`: carpetas de Frigate y URLs de sus cámaras;
   - `deploy/camera-inventory.local.json`: inventario de cámaras;
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

En curso: la presentación en las 9 pantallas, en la rama
`codex/batcomputer-presentation`, subida a GitHub el 2026-10-05 y sin mergear.
Ya están hechas y aprobadas las tres pantallas pequeñas. Ahora toca el editor
de espacios con el mapa en vivo, en cuatro partes (ver la presentación más
arriba). La 2a, el plano, está hecha, y el editor (2b) está hecho, con la
vista adicional de las cámaras, y el propietario ya dibujó en él las 3 salas,
sus cámaras y sus puertas. El mapa en vivo (2c) está aprobado. Después
viene el cambio al plano nuevo (2d), según `docs/diseno-batcomputer.md`.

El editor está en `http://127.0.0.1:8092/editor` una vez reiniciado el
servicio de pantallas (en el siguiente inicio de sesión). Mientras tanto se
puede revisar con un servidor aparte que guarda en el mismo plano:
`python services\batcomputer-ui\server.py --port 8093` y
`http://127.0.0.1:8093/editor`.

Para verlas en este PC: `http://127.0.0.1:8092/preview`, o una pantalla en
`http://127.0.0.1:8092/screen/<pantalla>` (`?demo` en las de salud). El
supervisor arranca el servicio de pantallas desde el siguiente inicio de
sesión; a mano: `python services\batcomputer-ui\server.py`. La recuperación
ante fallos (4.4) sigue pendiente y no depende de la estética.

Pendiente del propietario: la revisión de privacidad antes de usar la
comparación facial (ocupación v2) con visitantes.
