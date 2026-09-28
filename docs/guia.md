# Guía del sistema: conceptos clave

Esta guía no es un manual. Explica solo las ideas necesarias para entender
qué hace el sistema y por qué está construido así. Los detalles técnicos de
cada módulo están en su README, y el estado del proyecto en
[`ROADMAP.md`](../ROADMAP.md).

---

## 1. El recorrido de una persona

La forma más fácil de entender el sistema es seguir a alguien:

1. **Entra en Recepción.** La Hikvision envía su vídeo a Frigate, que corre en
   el PC. Frigate detecta a una persona, le asigna un número temporal (por
   ejemplo `r-17`) y avisa: "nueva persona en Recepción".
2. **Ese aviso se traduce.** El adaptador de Frigate lo convierte al *idioma
   común* del sistema (`track_update.v1`) y lo envía por MQTT, que es el
   sistema de mensajería.
3. **Se guarda.** El receptor escribe cada mensaje en una base de datos. Es
   la memoria del sistema: todo lo demás se puede reconstruir a partir de
   ella.
4. **Se interpreta.** El tracking engine sabe que la cámara de Recepción
   cubre la sala "Recepción" y suma uno a su ocupación.
5. **Pasa a la sala de reuniones.** Frigate cierra `r-17` porque ya no lo ve.
   Unos segundos después, otra cámara de la sala de reuniones ve a alguien.
   Como hay una puerta entre las dos salas y el tiempo encaja, el tracking
   engine propone un **handoff**: "probablemente es la misma persona".
6. **El mapa lo muestra.** Una persona menos en Recepción, una más en
   Reuniones y una flecha de transición.

Si esa segunda cámara es una Dahua, el camino cambia un poco: la IA la hace la
propia cámara y el colector Dahua recibe sus eventos y fotos directamente,
sin pasar por Frigate. El resto del recorrido es igual, gracias al idioma
común.

---

## 2. Detectar, seguir e identificar son tres cosas distintas

Este es el concepto más importante del proyecto.

**Detectar** es mirar *un* fotograma y decir "aquí hay una persona, dentro de
este rectángulo". Un detector puede equivocarse de dos maneras:

- *Falso positivo:* ve una persona donde no la hay. En el showroom pasa con
  la figura del robot o con una persona que aparece en una pantalla de TV.
- *Falso negativo:* no ve a una persona que sí está, por ejemplo porque la
  tapan los respaldos de las sillas.

**Seguir** (*tracking*) es unir las detecciones de fotogramas consecutivos
para saber que ese rectángulo que se mueve es la misma persona. El resultado
es un **track**, con un número temporal. Ese número solo tiene sentido
**dentro de una cámara** y durante un tramo de tiempo. Si la persona sale y
vuelve a entrar, recibe otro número. Por eso nunca decimos que un `ObjectID`
de Dahua es "una persona": es un *track local*.

**Identificar entre cámaras** es proponer que el track de la cámara A y el de
la cámara B son la misma persona. Siempre es una hipótesis, nunca una
certeza. El sistema puede responder "pendiente" o "no sé", y eso es mejor que
una identidad inventada.

---

## 3. Por qué un track se rompe y por qué, para contar, importa menos

Cuando alguien queda tapado un momento, el tracker puede perderlo y darle un
número nuevo al reaparecer. A eso se le llama **fragmentación**:

```text
tiempo →        0s        10s        20s        30s
persona real    ██████████████████████████████████
track 41        ██████████████
track 44                        ████████████████████
```

Parece un problema grave, pero mira el conteo: en cada instante hay **un solo
track activo**. La ocupación de la sala sigue siendo 1. La fragmentación
molesta para reconstruir el recorrido, pero apenas afecta al conteo.

Los errores que sí rompen el conteo son otros dos:

- **Duplicado:** dos tracks *a la vez* para la misma persona. La sala marca
  2 cuando hay 1.
- **Fusión:** un solo track que empieza en una persona y termina en otra.
  Mezcla identidades sin que nadie lo note.

Por eso el criterio propuesto para aceptar el tracking es "conteo correcto
y cero fusiones". La fragmentación se mide y se informa, pero no bloquea: se
repara más arriba, uniendo fragmentos con tiempo, geometría y apariencia.

---

## 4. Eventos "al final" frente a eventos "en vivo"

No todas las fuentes avisan en el mismo momento:

- **Frigate** avisa en vivo: `new` cuando aparece alguien, `update` mientras
  se mueve y `end` cuando desaparece.
- **Dahua** (el evento `HumanTrait`) avisa **al final**, cuando la persona ya
  se fue, pero con las mejores fotos de cuerpo, cara y contexto.

Para un mapa en tiempo real, un aviso que llega 20–30 segundos tarde no
sirve como posición. Sí sirve como evidencia de calidad. La fase 2 del roadmap
comprueba si las Dahua pueden dar también posiciones en vivo (los datos
"IVS" que su propia web usa para dibujar recuadros). De eso depende que 5 de
las 8 cámaras necesiten que el PC haga detección o no.

---

## 5. El idioma común

Cada marca habla distinto: Dahua usa su SDK y su CGI, y Frigate usa MQTT con
su propio formato. Si el resto del sistema tuviera que entender cada formato,
cada cámara nueva obligaría a tocarlo todo.

Por eso cada fuente tiene un **adaptador** que traduce a un contrato común
(`contracts/track-update-v1.schema.json`). A partir de ahí nadie sabe ni le
importa la marca. El mensaje original se conserva aparte, para poder auditar
y reinterpretar después.

---

## 6. No perder mensajes

Las cámaras se reinician, la red falla y los programas se cuelgan. El sistema
está diseñado para que eso no haga perder ni duplicar datos:

- **Outbox** (bandeja de salida): cada adaptador guarda el mensaje en disco
  *antes* de enviarlo. Si el envío falla, lo reintenta más tarde.
- **MQTT con QoS 1:** el mensaje se reenvía hasta que llega el acuse de
  recibo, como un correo certificado.
- **Deduplicación:** cada mensaje tiene un identificador único. Si llega dos
  veces, el receptor lo guarda una sola.

La consecuencia práctica: se puede reiniciar cualquier pieza sin perder
información.

---

## 7. Salas, puertas y handoffs

El **Space Mapper** es donde se describe el lugar: las salas, dónde está
cada cámara, qué sala cubre y qué **transiciones** existen (puertas o
pasillos), cada una con un tiempo de tránsito mínimo y máximo.

Con eso, el tracking engine propone un **candidato de handoff** cuando un
track termina en una sala y otro empieza en una sala conectada dentro de ese
margen de tiempo. La puntuación mide solo si el tiempo encaja. La evidencia
visual (parecido de ropa o de cara) se calcula en un canal aparte. Nada de
esto es una identidad confirmada.

Esto no depende de dónde estén las cámaras: el plano de salas y puertas sale
del edificio. Lo que sí depende de las cámaras son los tiempos reales de
tránsito, que se calibran al final.

---

## 8. Por qué la posición de la cámara lo cambia todo

Las grabaciones de prueba lo muestran bien:

- Una cámara a la altura de la mesa ve a las personas tapadas por las
  sillas. Ningún detector puede ver lo que no está en la imagen.
- Una pantalla dentro del encuadre produce "personas" que están en la TV.
- Una persona lejana ocupa pocos píxeles y se detecta peor.

La regla del proyecto es: **construir ahora todos los mecanismos y dejar los
parámetros como configuración**. Lo que se mide en la imagen (máscaras,
umbrales, tiempos, elección de modelo) se calibra después de colocar las
cámaras, con una prueba de aceptación corta. Antes de colocarlas, una guía de
colocación ayuda a elegir buenas posiciones.

---

## 9. Privacidad

- El sistema es anónimo: cuenta y relaciona tracks, no pone nombres.
- Las fotos de cuerpo y cara se usan solo como evidencia técnica y se borran
  a los 7 días.
- Los datos del sitio (credenciales, IP, grabaciones y fotos) nunca se suben
  al repositorio, porque es público.

---

## Glosario breve

| Término | Significado |
|---|---|
| RTSP | Protocolo con el que las cámaras entregan su vídeo |
| NetSDK / CGI | Las dos vías oficiales para hablar con una Dahua: su librería y su API web |
| Frigate | Software abierto que recibe vídeo, detecta, sigue objetos, graba clips y publica eventos |
| go2rtc | Componente de Frigate que se conecta una sola vez a cada cámara y reparte el vídeo |
| MQTT | Sistema de mensajería; Mosquitto es el servidor que lo implementa |
| Track | Seguimiento de una persona dentro de una cámara, con un número temporal |
| Handoff | Hipótesis de que un track de una cámara continúa en otra |
| ReID | Comparar la apariencia para ayudar en los handoffs |
| ONNX / DirectML | Formato de modelos de IA y la vía de Windows para ejecutarlos en la GPU |
| Homografía | Transformar la posición en la imagen en una posición en el suelo |
