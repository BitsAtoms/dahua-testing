# Diseño de las pantallas del Batcomputer

Borrador para aprobar, 2026-10-01. Recoge las decisiones del propietario y lo
que queda por decidir. La muestra visual está en
`services/batcomputer-ui/web/style-sample.html` y las reglas de estilo, en
`services/batcomputer-ui/README.md`.

## 1. La idea

El PC final está en una réplica del ordenador de Batman con 9 pantallas.
**Cada pantalla muestra una parte distinta del sistema**: las cámaras, el
mapa, los eventos, los recorridos y la salud de las piezas. Así, quien lo mira
entiende de un vistazo qué hace el proyecto.

Principios:

- **Poco texto y funciones justas.** Lo que no ayuda a entender, sobra.
- **Solo dos pantallas se usan con el ratón**: las dos de trabajo. Las otras
  siete solo muestran información.
- **Mostrar personas, no datos internos.** El mapa enseña cuánta gente hay y
  por dónde pasa. Los números de track y las puntuaciones van a la pantalla
  de recorridos.
- **Anónimo.** Nunca hay nombres. Los recorridos son hipótesis, no
  identidades.

## 2. Las 9 pantallas

```text
                 ┌─ top_left ───────────┐ ┌─ top_right ──────────┐
┌ side_left ─┐   │ VIDEOVIGILANCIA      │ │ MAPA EN VIVO         │   ┌ side_right ┐
│ EVENTOS    │   │ todas las cámaras    │ │ hasta 3 plantas      │   │ RECORRIDOS │
│ con fotos  │   └──────────────────────┘ └──────────────────────┘   │ + RESUMEN  │
│ + registro │   ┌─ bottom_left ────────┐ ┌─ bottom_right ───────┐   │ DEL DÍA    │
│ Dahua      │   │ TRABAJO (Windows)    │ │ TRABAJO (Windows)    │   │            │
└────────────┘   └──────────────────────┘ └──────────────────────┘   └────────────┘
  [SALUD · PARTES]        [CONSOLA DEL SUPERVISOR]        [SALUD · CÁMARAS]
```

| Pantalla | Qué muestra | Ratón |
|---|---|---|
| `top_left` | Videovigilancia: el vídeo en directo de todas las cámaras, con el análisis encima | No |
| `top_right` | Mapa en vivo de las plantas del edificio | No |
| `side_left` | Eventos de todas las cámaras con sus fotos, y el registro del colector Dahua | No |
| `side_right` | Recorridos entre salas y resumen del día | No |
| `bottom_left`, `bottom_right` | Libres: escritorio de Windows para trabajar (aquí se abre el editor de espacios) | Sí |
| `mini_left` | Salud de las partes del sistema | No |
| `mini_center` | Consola del supervisor: fondo negro, solo texto amarillo | No |
| `mini_right` | Salud de las cámaras: si cada una tiene conexión | No |

Las 6 grandes son monitores de 32" 4K. Las pequeñas son de 1080p y unas 15".
Todas tienen casi la misma densidad de píxeles, así que las ventanas se
dibujan con una escala fija ×2. Una letra mide lo mismo en todas las
pantallas.

## 3. Estilo

Batcomputer moderno con toques del Batman clásico y de los 90:

- **Predominan los negros**, con el amarillo como contraste.
- **El rojo solo aparece en lo crítico.**
- **Ningún estado se distingue solo por el color**: cada uno lleva su icono,
  su palabra y su forma.
- **Detalles de época**: franjas amarillas y negras como las de la consola
  física, y líneas de barrido y brillo de monitor antiguo, muy suaves. La
  intensidad se revisa en las pantallas reales.

Las reglas exactas y los contrastes medidos están en el README del módulo.

## 4. Pantalla por pantalla

### Videovigilancia (`top_left`)

- Cuadrícula de 3×3: caben las 7 cámaras actuales y las 2 previstas. Cada
  imagen sale a 1280×720 píxeles reales.
- Sobre cada vídeo, un recuadro amarillo por persona detectada, con su
  número de track.
- Una cámara sin conexión se ve en rojo, con "SIN CONEXIÓN".
- Se usan los streams secundarios, de baja resolución, para que decodificar
  9 vídeos cueste poco.
- Las Dahua no pasan hoy por Frigate. Se añadirán a go2rtc solo para verlas,
  como el portero de la puerta: es un cambio en la configuración de Frigate
  que hace el propietario.
- Los recuadros salen del seguimiento propio del sistema, así que las Dahua y
  las de Frigate se ven igual.

### Mapa en vivo (`top_right`)

- Las plantas del edificio, una al lado de otra. Hasta 3 caben en la pantalla.
- Cada sala muestra su número de personas y **un punto por persona**,
  ordenados como asientos. No hay posiciones inventadas: no se sabe en qué
  punto exacto de la sala está cada uno.
- Cuando alguien cambia de sala, su punto **cruza la puerta** con una
  animación. Si cambia de planta, sale por la escalera de una y aparece en la
  otra.
- Las entradas del edificio se marcan en el borde, hacia el "Exterior".
- Sin números de track ni puntuaciones: eso va en `side_right`. Así se evitan
  los puntos amontonados del monitor actual.

### Eventos (`side_left`)

- Lista de las últimas detecciones de todas las cámaras:
  - las Dahua, con sus fotos de cuerpo, cara y contexto;
  - las de Frigate, con su instantánea.
- Debajo, el registro del colector Dahua en amarillo sobre negro.

### Recorridos y resumen del día (`side_right`)

- Arriba, la línea de tiempo de los pasos entre salas que propone el sistema
  ("Recepción → Reuniones · probable"). Los dudosos aparecen como
  "pendiente".
- Abajo, el resumen del día: visitas, personas ahora, hora punta, estancia
  media y sala más concurrida.

### Las tres pequeñas

- `mini_left`: salud de las partes. Docker, Mosquitto, Frigate, el detector
  (con las alarmas de ciego, atascado y roto), los servicios y el retraso del
  transporte.
- `mini_center`: la consola del supervisor, en vivo.
- `mini_right`: la conexión de cada cámara.

## 5. Modo privado

- **`Ctrl+Alt+X` tapa con estática todo lo que muestra a personas**: los
  vídeos de la videovigilancia y las fotos de los eventos, con el cartel
  "SEÑAL CENSURADA".
- Se activa con una tecla de la Stream Deck (acción "Tecla de acceso rápido")
  y funciona aunque estés trabajando en otra ventana.
- Propuesta: **recuerda el último estado** al reiniciar. Si estaba tapado,
  sigue tapado.
- Solo cambia lo que se ve: el sistema sigue analizando y guardando igual.

## 6. Ratón y atajos

- El ratón se queda **encerrado en las dos pantallas de trabajo**. Se hace con
  un programa que vigila el puntero, sin tocar la disposición de Windows.
- Ayudas de Windows, que se activan una vez en el Batcomputer:
  - "mostrar la ubicación del puntero al pulsar Ctrl";
  - un puntero más grande, en amarillo.

| Atajo | Qué hace |
|---|---|
| `Ctrl+Alt+X` | Modo privado: tapa o destapa vídeos y fotos |
| `Ctrl+Alt+E` | Abre el editor de espacios en una pantalla de trabajo |
| `Ctrl+Alt+L` | Suelta o vuelve a encerrar el ratón |
| `Ctrl+Alt+M` | Lleva el ratón al centro de `bottom_left` |
| `Ctrl+Alt+B` | Vuelve a colocar las ventanas de las 7 pantallas (por si se cerró alguna) |

Cualquiera de ellos se puede poner en una tecla de la Stream Deck.

## 7. Editor de espacios

Sirve para describir el edificio: sus plantas, sus salas, dónde está cada
cámara y por dónde se pasa de una sala a otra. Se abre en una ventana normal
de una pantalla de trabajo, con el mismo estilo.

### Cómo se organiza

```text
Espacio de trabajo (un edificio u oficina)
 └─ Plantas (hasta 3), cada una con su plano
     ├─ Salas: rectángulos o formas libres, sobre la rejilla
     ├─ Cámaras: colocadas en una sala, con su dirección
     ├─ Puertas: entre dos salas, o hacia el Exterior (entradas del edificio)
     └─ Conexiones con otra planta (escalera o ascensor; solo una conexión)
```

Ahora habrá un solo espacio de trabajo, el edificio actual. Llevar el sistema
a otra oficina es crear otro espacio de trabajo.

### Cómo se usa

```text
┌ PLANTAS ───┐┌──────────────────────────────────────────────────────────┐
│ ▸ PLANTA 0 ││ [▭ SALA] [◉ CÁMARA] [⇥ PUERTA] [⇅ OTRA PLANTA] [⌫] [↶] │ [GUARDAR ●]
│   PLANTA 1 │├──────────────────────────────────────────────────────────┤
│   + nueva  ││  · · · · · · · · · · · · · · · · · · ·                   │
│            ││  · ┌─────────┐┌──────────┐ · · · · ·    sin colocar:    │
│            ││  · │RECEPCIÓN⇥ REUNIONES │ · · · · ·    [CAM-06]        │
│            ││  · │   ◉     ││    ◉     │ · · · · ·    [CAM-07]        │
│            ││  · └───⇥─────┘└────⇅─────┘ · · · · ·                    │
│            ││  ·  EXTERIOR    → Planta 1  · · · · ·    ▲ Almacén       │
│            ││  · · · · · · · · · · · · · · · · · · ·      sin cámara   │
└────────────┘└──────────────────────────────────────────────────────────┘
```

- **Plantas:** están en la columna izquierda. Un clic abre una planta para
  verla y editarla; "+" crea otra. No hay que buscar lo guardado: todo está a
  la vista.
- **Rejilla:** todas las esquinas se ajustan a los puntos de la rejilla. Las
  salas quedan alineadas y simétricas, y dos salas vecinas comparten pared
  sin esfuerzo.
- **Sala:**
  - **arrastrar** dibuja un rectángulo;
  - **hacer clic en cada esquina** dibuja una forma libre, como una L. La
    rejilla mantiene las paredes rectas.
  - Un clic en una sala permite **renombrarla** o borrarla, y se pueden
    arrastrar sus esquinas.
- **Cámara:** se arrastra de la bandeja de "sin colocar" a una sala y se gira
  con un tirador. Toma sola la sala donde la sueltas. Si una sala la ven
  varias cámaras, se marca, para no contar dos veces a la misma persona.
- **Puerta:** un clic en la pared entre dos salas. Si la pared da afuera, es
  una entrada del edificio.
- **Otra planta:** se elige una sala de esta planta y otra de otra planta
  (escalera o ascensor).
- **Avisos:** en una esquina, cortos: sala sin cámara, cámara sin sala, sala
  aislada.
- **Guardar:** un punto avisa de que hay cambios sin guardar, y cada guardado
  hace una copia de la versión anterior. `↶` deshace.

**Desaparece:**

- las sesiones de prueba y sus anotaciones;
- la clasificación visual;
- la opción "cámara fija o móvil";
- los tiempos de paso escritos a mano (ver el punto 8).

El plano real del edificio es un dato del sitio: se guarda solo en el PC
(`runtime/`), nunca en Git.

## 8. Tiempo entre cámaras y zonas de la imagen (propuesta)

Saber solo que dos salas están conectadas da poca información. Lo útil es
saber **por dónde sale una persona de la imagen de una cámara y por dónde
entra en la siguiente**, y cuánto se tarda de verdad entre las dos.

- Al crear una puerta, se indica opcionalmente por qué borde de la imagen de
  cada cámara se ve esa puerta: izquierda, derecha, arriba, abajo o centro.
  Se elige con un clic sobre una foto de la cámara.
- El sistema **mide** los tiempos reales de cada puerta y aprende su margen
  habitual. No se escribe ningún tiempo a mano, y la puerta muestra, como
  información, "normalmente entre 4 y 9 s".
- Mientras no haya medidas, se usa un margen general amplio.
- Depende de dónde quede cada cámara: se construye ahora y se calibra al
  colocarlas (fase 7).

## 9. Cómo se construye

- **Un servicio nuevo, `batcomputer-ui`**, gestionado por el supervisor como
  los demás. Hace cuatro cosas:
  - sirve una página por pantalla y el editor;
  - reúne los datos de las otras piezas;
  - guarda el estado del modo privado;
  - escucha los atajos globales y vigila el ratón.
- **El supervisor** pone su salud en un archivo que leen `mini_left` y
  `mini_right`. Es la parte de datos del panel de salud (paso 4.2).
- **El lanzador** abre las 7 ventanas de exhibición. Cada una es una ventana
  de Chrome con su propio perfil, en su pantalla, a pantalla completa y con la
  escala ×2. Las pantallas se reconocen por su identificador estable, y no por
  `\\.\DISPLAYn`, que puede cambiar. Si es posible, cada ventana se dibuja con
  la tarjeta gráfica de su propia pantalla.
- **En este PC**, que tiene una sola pantalla, una vista previa muestra las 9
  pantallas a escala con datos reales. Lo que depende de varias pantallas (la
  colocación, el ratón y las tarjetas gráficas) se prueba en el Batcomputer.

### Orden propuesto

1. **Base:** el servicio, el lanzador, la vista previa y las tres pequeñas
   (consola y salud). Prueba pronto la colocación de ventanas en el
   Batcomputer.
2. **Editor de espacios y mapa en vivo:** las plantas, la rejilla, las puertas
   y el Exterior, con el mapa nuevo de puntos por persona.
3. **Videovigilancia:** con el modo privado, los atajos y el ratón encerrado.
4. **Eventos.**
5. **Recorridos y resumen del día.**
6. **Más adelante:** las zonas de la imagen y los tiempos medidos (punto 8).

## 10. Pendiente de decidir

- Aprobar este documento, o lo que haya que cambiar.
- El punto 8 (zonas de la imagen y tiempos medidos): ¿adelante?
- El orden de construcción.
- El modo privado: ¿recuerda el último estado al reiniciar?
