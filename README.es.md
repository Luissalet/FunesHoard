# El Tesoro de Funes
### ¿Dónde estaba? ¿Qué hacía antes de comer? ¿Cuánto de esta semana fue realmente para Faustus?
**La memoria episódica de tu ordenador: aplicación en primer plano, ventana, archivos abiertos y commits realizados, guardados en un SQLite local y filtrados por privacidad antes de escribir nada.**

[English](README.md) · [Ejecutar en local](#ejecutar-en-local-en-windows) · [Conectar una IA](docs/MCP.md) · [Portfolio](https://luissalet.github.io/Portfolio/#projects)

![Vista Hoy: línea de tiempo del día por categoría, totales activo/ausente y bloques de foco](docs/media/today.png)
*Aplicación real, tres días de datos de demostración sintéticos (`--demo`, sin títulos de ventana reales).*

## Por qué

Un asistente local con un modelo de 27B y ninguna memoria del escritorio
solo puede adivinar cuando le preguntas "¿dónde estaba?" o "¿cómo fue el
día?". No tiene ningún registro de lo que realmente tenías delante, así que
o se lo inventa o te pide que se lo expliques todo desde cero, cada vez. El
Tesoro de Funes graba lo único que un modelo de lenguaje no puede ver por
sí mismo -- la secuencia de lo que estaba en pantalla -- lo convierte en
tramos, resúmenes del día y bloques de foco, y responde a esas preguntas
con datos en vez de suposiciones.

## Qué está implementado

| Área | Disponible ahora | Límite |
| --- | --- | --- |
| Recolección | Aplicación/ventana/inactividad/bloqueo en primer plano muestreados a 1 Hz; fusionados en tramos activo/ausente/bloqueado; el tiempo ausente se retro-fecha al momento real en que cesó la entrada; se detectan saltos de suspensión/hibernación sin unirlos; el tramo abierto se vuelca cada 30s (un cuelgue pierde <30s) | El recolector de Windows usa `ctypes`/`psutil`; el de Linux (solo desarrollo, mejor esfuerzo vía `xdotool`/`xprintidle`) no es el objetivo de producción |
| Privacidad | Las reglas de exclusión descartan una muestra antes de guardarla (gestores de contraseñas, navegación privada/incógnito, ES+EN); las de redacción sustituyen solo el título (banca/login, ES+EN); pausa de 15min/1h/hasta reanudar con expiración automática; purga por retención; borrado de un rango; exportación JSON | No se extrae el **dominio** del navegador a partir del título (poco fiable solo con texto) -- ver Límites más abajo |
| Clasificación | Reglas ordenadas por app/regex de título/dominio con valores por defecto para apps comunes de Windows; detección de proyecto desde títulos de VS Code y nombres de repositorios git conocidos; previsualización en vivo ("esta regla reclasificaría N tramos") antes de aplicar; reaplicar al historial como tarea en segundo plano, versionado para que las lecturas sigan siendo baratas | El análisis de títulos de JetBrains es genérico (heurística "A - B"), no específico del IDE |
| Conocimiento derivado | Resúmenes de día/semana/rango (activo/ausente, por categoría/app/proyecto, primera/última actividad), cambios de contexto (>=10s de permanencia), bloques de foco (>=25 min, interrupciones <=2 min), "dónde estaba" (retomar contexto) fusionando por proyecto | — |
| Otras fuentes | Archivos recientes vía un analizador binario de `.lnk` (Shell Link) escrito desde cero -- sin dependencia de COM; commits de git escaneados desde raíces de repositorio configuradas, filtrados por autor | El lector opcional de UI Automation para la barra de direcciones del navegador (objetivo ambicioso del spec) no se construyó: la detección de proyecto/categoría por título ya cubre el caso común, y un lector robusto multi-navegador es un proyecto en sí mismo -- se documenta aquí como descartado deliberadamente, no como olvidado |
| Búsqueda | SQLite FTS5 sobre títulos/archivos/asuntos de commits, con resaltado de fragmentos | Recurre a búsqueda por subcadena (`LIKE`) si la build de `sqlite3` de la plataforma no tiene FTS5 (se comprueba al arrancar) -- la build de Linux usada aquí sí lo tiene; **no verificado en la build de Windows 3.13 de python.org**, ver riesgo de Windows más abajo |
| API del agente | Solo lectura salvo `activity_pause`; cada llamada se registra y se muestra en "Actividad del asistente"; verificado con un cliente MCP real por stdio contra una app en marcha | — |
| Interfaz | Hoy (línea de tiempo, totales, bloques de foco), Semana, Buscar, Proyectos, Archivos y commits, Reglas (con previsualización en vivo), Privacidad, Actividad del asistente; ES/EN; claro/oscuro | — |

Más pantallas: [Privacidad](docs/media/privacy.png) (controles de pausa,
reglas de exclusión/redacción, retención) · [Buscar](docs/media/search.png)
(FTS5 con resaltado de fragmentos) · [Actividad del asistente](docs/media/assistant-activity.png)
(cada llamada del agente, auditable).

## Conectarlo a Faustus

La app se declara con `faustus-plugin.json`. Arráncala y en Faustus ve a
**Conectores -> Apps cercanas -> Añadir**.

| Herramienta | Qué hace | ¿Solo lectura? |
| --- | --- | --- |
| `activity_now` | App/título/proyecto actual, segundos de inactividad, estado de grabación | sí |
| `activity_where_was_i` | Retomar contexto: últimos contextos de trabajo antes de un momento, con archivos/commits | sí |
| `activity_timeline` | Tramos fusionados de un rango | sí |
| `activity_summary` | Totales de día/rango, bloques de foco, cambios de contexto | sí |
| `activity_search` | Buscar cuándo apareció un título/archivo/commit | sí |
| `activity_recent_files` | Archivos abiertos recientemente | sí |
| `activity_projects` | Tiempo por proyecto, última vez, commits | sí |
| `activity_pause` | Pausar la grabación (no puede reanudar antes, cambiar reglas, borrar ni exportar) | **no** (la única escritura) |

También funciona con cualquier cliente MCP por stdio -- ver
[docs/MCP.md](docs/MCP.md) para la referencia completa y un ejemplo de
configuración.

## Ejecutar en local (en Windows)

Haz doble clic en **`Iniciar Funes's Hoard.cmd`** (o ejecuta
`scripts\start.ps1`), que crea el venv, instala las dependencias fijadas y
construye el frontend la primera vez, y arranca la app en
`http://127.0.0.1:8813`.

Pasos manuales:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-lock.txt
cd frontend; npm ci; npm run build; cd ..
.venv\Scripts\python -m funes_hoard
```

Añade `--demo` para funcionar con datos sintéticos en `data-demo/` en vez
de grabar tu escritorio real (`--data-dir` y `--no-browser` también están
disponibles; ver `python -m funes_hoard --help`).

## Arquitectura

FastAPI + un hilo recolector a 1 Hz + SQLite (WAL), con lógica central pura
y testeada (construcción de tramos, privacidad, clasificación, resúmenes)
que nunca importa FastAPI ni sqlite. Ver
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Pruebas

```
.venv/bin/python -m pytest tests/ -q
```

**85 pruebas pasan en unos 10 segundos**, cubriendo: fusión de tramos y
retro-fechado de ausente/bloqueado, gestión de saltos de suspensión,
volcado seguro ante cuelgues, exclusión de privacidad (comprobando que la
fila nunca llega a existir) y redacción, expiración de la pausa, purga por
retención, el analizador de `.lnk` contra un fixture construido en la
propia prueba (un bug real -- `LocalBasePathOffset` se leía del offset de
byte equivocado según MS-SHLLINK -- se detectó y corrigió gracias a esta
prueba), clasificación y detección de proyecto, bloques de foco y cambios
de contexto, parseo de palabras de fecha (hoy/ayer/esta semana/-2h), toda
la superficie de FastAPI incluyendo el guardián anti-ataques de navegador,
la comprobación del manifiesto, y una prueba de protocolo MCP de extremo a
extremo que lanza `mcp_server.py` como un subproceso real contra una app en
marcha.

`npm ci && npm run build` (en `frontend/`) pasa sin ningún error de
TypeScript.

## Privacidad y límites

- Solo escucha en `127.0.0.1`. Sin telemetría, sin llamadas de red salientes
  salvo el escaneo de commits de git (`git log` local, sin red) y las
  peticiones de esta interfaz a sí misma.
- Las reglas de exclusión se ejecutan **antes** de que una muestra llegue a
  escribirse en disco; esto se comprueba afirmando que la fila nunca existe,
  no solo que se borra después.
- El acceso de escritura del agente es exactamente una acción (pausar) y
  nada más; las rutas enumeradas `/api/agent/<tool>` son toda la superficie
  de cara al agente, verificado con una prueba que comprueba que cualquier
  otra ruta de escritura bajo ese prefijo devuelve 404.
- Los resultados están acotados (5-100 elementos según la herramienta) con
  un indicador explícito `truncated`/`has_more`, ya que el consumidor
  previsto es un modelo local pequeño con contexto finito.

### Límites (cosas que deliberadamente no hace)

- No extrae el dominio del navegador a partir de títulos (poco fiable sin
  un lector de UI Automation; se descartó en vez de entregarse a medias).
- La purga por retención, además de los tramos, también limpia
  `file_events` y `commits` en la misma ventana -- una ampliación
  deliberada respecto a "solo tramos" para que "borrar mi historial"
  signifique eso de verdad; ver `docs/ARCHITECTURE.md`.
- La búsqueda FTS5 recurre a búsqueda por subcadena si no está disponible;
  ver la nota de riesgo de Windows más abajo.

### Riesgo específico de Windows no comprobable desde este entorno Linux

Esto se ha construido y probado en Linux (Python 3.11); el objetivo de
producción es Windows (Python 3.13 en `C:\Python313`). Dos cosas no se han
podido verificar aquí y conviene comprobarlas en la primera ejecución real
en Windows:

1. **Disponibilidad de SQLite FTS5** en la build de Windows 3.13 de
   python.org. La app lo comprueba al arrancar y recurre automáticamente a
   búsqueda por subcadena si falta (`Database.fts_available`, ver
   `docs/ARCHITECTURE.md`), así que la búsqueda funciona de todos modos --
   pero la *calidad* del ranking cambia.
2. **`WindowsProbe`** (llamadas `ctypes` a `user32`/`kernel32`, más
   `psutil`) no puede ejecutarse en absoluto en Linux; su lógica de
   detección de inactividad y de pantalla bloqueada solo se ejercita en las
   pruebas a través de la interfaz `Probe` con un falso, nunca contra
   llamadas Win32 reales.
