# AGENTS.md

Reglas para agentes de código que trabajen en este repositorio.

## Antes de tocar nada

- Lee `docs/ARCHITECTURE.md` y `funes_hoard/api.py` antes de añadir un
  endpoint o una tabla nueva.
- `funes_hoard/core/*` no debe importar FastAPI ni `sqlite3`. Es lógica
  pura y así se mantiene testeable sin levantar la app.
- `funes_hoard/mcp_server.py` es un script independiente: solo puede
  importar stdlib, `httpx` y `mcp`. Nunca `from funes_hoard import ...`.
- `funes_hoard/hoard_link/` es una copia exacta de otro repositorio
  (`hoard-link`, ver `VENDORED.txt`). **Nunca la edites**: cualquier
  necesidad nueva va en `funes_hoard/backend.py`, que la envuelve. Para
  actualizarla, sustituye la carpeta entera y corre `pytest tests/test_backend.py`.
- Cualquier función nueva que use un modelo (LLM, visión...) pasa por
  `app.state.link` (`Link` de Hoard Link), nunca carga un servidor propio.
  Debe seguir funcionando -- desactivada con una razón legible -- cuando
  no hay ningún modelo disponible, y sus pruebas usan `tests/fakes.py::FakeLink`,
  nunca una red real.

## Al añadir una capacidad para el agente

1. Implementa la lógica en `core/` o `queries.py` (puro, testeado).
2. Expón un endpoint `/api/agent/<tool>` en `api.py` que llame a esa
   función y registre la llamada en `agent_calls`.
3. Añade la herramienta en `mcp_server.py` como una llamada HTTP a ese
   endpoint, con docstring + línea `Keywords:` en inglés y español.
4. Si la herramienta escribe algo, pregúntate si de verdad hace falta:
   el contrato es "solo lectura salvo pausar". Si no es pausar, probablemente
   no debería ser una herramienta del agente.
5. Los errores se lanzan como `BadInput(code, message)` con un mensaje que
   diga qué pasar en su lugar; nunca un 500 por una entrada del modelo.
6. Actualiza `docs/MCP.md`, `skills/where-was-i/SKILL.md` y el test de
   protocolo MCP (`tests/test_mcp_protocol.py`).

## Pruebas

- `pytest -q` debe tardar menos de 90 s. Si una prueba nueva es lenta,
  sospecha primero de una conexión sqlite nueva por llamada (ver
  `docs/ARCHITECTURE.md`, sección Database) antes de asumir que hace falta
  mockear algo.
- Las pruebas que dependen de la hora usan "yesterday" o un `now` fijo: los
  datos de demo de hoy empiezan a las 09:00 y la suite tiene que pasar a
  cualquier hora.
- Las salidas del agente pasan por `queries.agent_view` (horas ISO locales,
  títulos recortados); la interfaz recibe segundos epoch. No mezcles ambas.
- El código específico de Windows (`collectors/windows.py`) debe poder
  **importarse** en Linux sin lanzar excepción; solo debe fallar al
  **instanciar** `WindowsProbe()` fuera de `sys.platform == "win32"`.

## Commits

- Identidad: `Luissalet <luissalet@users.noreply.github.com>`.
- Mensajes en inglés, prefijo convencional (`feat:`, `fix:`, `test:`,
  `docs:`, `chore:`), cuerpo explicando el porqué.
- Nunca menciones otras apps o competidores por nombre, ni datos
  personales, en un mensaje de commit.
