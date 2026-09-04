# Dahua collector: estado y continuación

Fecha del handoff: 2026-09-04.

## Estado alcanzado

El experimento ya prueba el recorrido real de la cámara Dahua:

```text
cámara
 -> login NetSDK
 -> suscripción HumanTrait con imágenes
 -> evento CGI con ObjectID
 -> correlación por GroupID
 -> relación cuerpo/rostro
 -> JPEG body, face y panorámicas
 -> mensajes observation.v1
 -> consola web local
```

La interfaz está en `experiments/dahua-netsdk/collector/web/index.html` y el
servicio que la entrega está en `collector/dashboard.py`. Al cerrar esta sesión
el servicio quedó detenido; no hay un listener activo en el puerto 8090.

La configuración real y los datos sensibles continúan fuera de Git:

```text
.env
experiments/dahua-netsdk/cameras.local.json
experiments/dahua-netsdk/output/
tests/dahua-events/output/
```

No se modificó el SDK oficial bajo `NetSDK/`.

## Cómo retomar

Desde la raíz del repositorio:

```powershell
python -m unittest discover -s tests\dahua-events -p "test_*.py" -v
python experiments\dahua-netsdk\collector\dashboard.py
```

Abrir `http://127.0.0.1:8090`. La consola inicia las cámaras habilitadas,
muestra conexión, observaciones, mensajes, latencia local y las últimas
capturas. `Ctrl+C` solicita la desconexión y limpieza del SDK.

Definiciones de las métricas actuales:

- `observaciones`: detecciones iniciales (`observation` o `new`).
- `mensajes`: observaciones más todos sus enriquecimientos `update`.
- `latencia`: tiempo entre la creación del mensaje normalizado y su recepción
  por el proceso de la consola. Todavía no es latencia cámara-a-navegador.

## Próximas tareas para terminar el collector

1. Añadir timestamps en CGI, NetSDK, receptor y navegador y mostrar latencia
   extremo a extremo y por etapa.
2. Sustituir el paso NetSDK `archivo -> notificación -> lectura Python` por un
   canal directo para metadatos; mantener los JPEG como archivos referenciados.
3. Acotar y medir las colas C++ y Python, definiendo una política explícita de
   backpressure para ráfagas.
4. Implementar el receptor local de `observation.v1`, con deduplicación por
   `message_id` y outbox SQLite para reintentos.
5. Separar en la consola los estados NetSDK y CGI, añadir historial corto de
   errores y contadores de reconexión/cola.
6. Convertir supervisor y consola en un servicio de Windows que arranque sin
   una sesión interactiva y siga activo aunque se cierre el navegador.
7. Ejecutar una prueba prolongada con cinco cámaras y registrar CPU, memoria,
   disco, eventos perdidos y percentiles p50/p95/p99 de latencia.

Para declarar terminado el módulo, la prueba prolongada debe confirmar
reconexión automática, apagado limpio, retención de siete días, ausencia de
pérdidas bajo la carga prevista y entrega de metadatos visibles por debajo del
objetivo acordado.

## Roadmap breve posterior

1. Crear un adaptador Frigate que escuche `frigate/events`, traduzca
   `new/update/end` a `observation.v1` y recupere snapshots por la API de
   Frigate sin modificar su código fuente.
2. Conectar Dahua y Frigate al mismo receptor local y validar eventos grabados
   de ambas fuentes con pruebas de contrato.
3. Implementar tracking por cámara, zonas y transiciones; después incorporar
   correlación visual entre cámaras como una capa independiente.
4. Persistir historial y publicar el estado agregado por WebSocket hacia la UI
   2D, manteniendo imágenes y datos pesados fuera del canal en tiempo real.
