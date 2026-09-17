# Auditoría del núcleo — 2026-09-16

## Alcance y resultado

Correcciones de integración sobre el árbol existente, sin commit, migraciones,
cambios de UI ni desarrollo de voz. La voz continúa desactivada por defecto.
Esto **no certifica todas las herramientas externas ni la generalización completa**.

## Causas demostradas

1. `Dispatcher._dispatch` ejecutaba WhatsAppText y local_fastpath antes del modelo.
   El primero guardaba un pendiente independiente y tenía un patrón compuesto de envío;
   el segundo podía interpretar la carpeta de destino como origen de búsqueda.
2. El fallback personal clasificaba una solicitud solo por contener palabras como
   “trabajo”. Se eliminó ese catchall, no se añadieron variantes lingüísticas.
3. El intérprete ocultaba fallos de proveedor con una pregunta genérica. Ahora
   conserva el pendiente y devuelve un fallo con diagnóstico saneado.
4. El dispatcher elevaba cualquier éxito del fast path a `verified`. Se retiró
   esa ruta. Las herramientas de prosa siguen siendo `executed`, no verificadas.
5. Una preferencia entre IDs de WhatsApp deducía identidad por igualdad de nombre.
   La prueba directa del resolver falló antes de retirar esa suposición. Dos IDs
   distintos siguen siendo ambiguos si no existe evidencia de equivalencia.
6. Groq real inicialmente pidió al usuario un nombre que podía descubrir con una
   consulta. Se corrigió la instrucción general de descubrimiento de datos y se
   validó una conversación real con archivos temporales.

## Entradas reales

| Entrada | Ruta |
|---|---|
| CHAT | `MainWindow._send` → callback → `_route_text_command_v1` → worker → `JarvisLive.dispatch_user_request` |
| HOME | `_send_home_command` → `_send(source='home')` → misma ruta |
| Remote | callback en `main.py` → `asyncio.to_thread(dispatch_user_request, text, 'remote')` |
| HUB | `PersonalPage.submit/execute` → `shared_dispatcher`, misma base local |
| Terminal | harness → `Dispatcher.dispatch_user_request`, mismo motor |

`_route_text_command_local` permanece como compatibilidad, pero no es fallback
del worker textual. WhatsAppText y las regex de filesystem ya no interceptan
el dispatcher. Los adaptadores deterministas de registros y tareas permanecen;
no se ha eliminado todo el parser antiguo. Las operaciones personales se invocan
por la herramienta registrada `personal_hub`; el modelo utiliza `personal_records`.

## Motor actual

Contexto → optimizaciones locales existentes → SemanticInterpreter → plan JSON
→ validación → confirmación → registro → resultado → contexto/evento UI.
No existe una clase activa GoalReasoningAgent en los Python inspeccionados: se
amplió SemanticInterpreter en lugar de introducir otro agente.

El ciclo admite hasta tres propuestas por turno, ocho pasos por plan y 50
elementos por foreach. Observa errores reales, permite replantear y bloquea una
llamada idéntica dentro del mismo turno, incluso si el modelo insiste. Mantiene
el resultado de pasos anteriores; no ofrece atomicidad entre distintas tools.
Las referencias anidadas solo recorren dict/list observados, sin eval ni atributos.

Las capacidades y sus esquemas se leen de ActionRegistry. Las confirmaciones
mantienen llamadas concretas y huellas de selección, con expiración de 120 s.
Los pendientes de herramientas conservan llamada, argumentos y candidatos.
OperationalContext conserva conversación acotada, recursos, resultados, último
fallo y plan. Los adaptadores personales/tareas todavía conservan referencias
de compatibilidad propias: su unificación completa queda pendiente.

ProviderManager prueba Groq textual, cliente local existente y Gemini textual.
Gemini usa el SDK instalado, sin herramientas automáticas ni Live. Los fallos
se sanean por categoría/código; no se publican claves ni respuestas HTTP crudas.
Groq fue evaluado realmente. Local y Gemini se comprobaron con fallos/respuestas
controlados en tests; su disponibilidad real no está certificada por esta tanda.

## Salud de herramientas

Inventario de `actions/*.py`: **21/21 registradas (100%)**, seis con handler
estructurado. Esto no incluye herramientas inline de Live ni plugins que la UI
anuncia por separado; no se debe confundir esa lista con las capacidades de texto.

| Tool | Directa | Verificación independiente | Estado |
|---|---|---|---|
| file_controller | list/find/move reales, temporal | origen y destino en disco | HEALTHY para esas acciones |
| personal_records | wallet CRUD/undo; work assign/update/read/return | SQLite real aislada | HEALTHY para esas acciones |
| kira_tasks | add/complete reales, archivo temporal | lectura del JSON persistido | HEALTHY directa; contrato solo executed |
| whatsapp_web | resolver/guardas/continuación con frontera de navegador simulada | no prueba de envío real | NOT VERIFIED LIVE |
| open_app/reminder y restantes | no ejecutadas en esta auditoría | no disponible | NOT TESTED |

**3/21 (14,29%)** tienen llamadas directas con verificación independiente en esta
auditoría. Dos tools emitieron además evidencia estructurada verified. No se
extrapola a acciones no probadas ni se etiqueta como rota una tool no ejecutada.
WhatsApp tenía la regresión del resolver descrita arriba; no se probaron mensajes reales.

## Validación

- Suite: 180 tests, **179 passed, 0 failed, 1 skipped** (evaluación online opt-in).
- Escenarios del motor con modelo grabado: 15 passed. Registry, filesystem y
  SQLite reales; no prueban capacidad lingüística de un proveedor.
- Health directo: cuatro pruebas passed, tres tools distintas.
- Modelo real: **una conversación passed**, consulta → referencia indirecta →
  movimiento de PDF, con hasta una aclaración permitida. Archivos sintéticos,
  sin acceso a datos personales. La primera evaluación pidió aclaración y no
  fue contada como éxito hasta comprobar la continuación y el disco.
- py_compile: 61 Python modificados/no rastreados del árbol actual.
- Sin cambios de esquema SQLite, sin restaurar ni borrar datos del usuario.
- `git diff --check` sin errores; `git status --short` revisado. No se hizo commit.

## Archivos de esta tanda

- Motor: `core/dispatcher.py`, `core/semantic.py`, `core/provider_manager.py`,
  `core/personal_commands.py`, `core/text_tools.py`, `core/text_tasks.py`,
  `core/local_fastpath.py`.
- Resolver: `actions/whatsapp_web.py`.
- Harness nuevos: `scripts/test_kira_agent.py`, `scripts/test_kira_tools.py`.
- Tests nuevos: `tests/test_agent_live.py`, `tests/test_tool_health.py`.
- Tests ajustados: `tests/test_runtime_regressions.py`, `tests/test_semantic_runtime.py`,
  `tests/test_local_dispatch_regressions.py`, `tests/test_text_runtime.py`,
  `tests/test_whatsapp_safety.py`.
- Documentación: este reporte y `docs/semantic-runtime.md`.

El resto del árbol modificado ya existía al comenzar y se conservó.

## Harness y pruebas manuales

```sh
.venv/bin/python scripts/test_kira_tools.py
.venv/bin/python scripts/test_kira_tools.py --smoke
.venv/bin/python scripts/test_kira_agent.py --interactive --db /tmp/kira-manual.db
KIRA_LIVE_EVAL=1 .venv/bin/python -m unittest discover -s tests -p test_agent_live.py
```

El harness de tools permite `--tool NAME --args JSON --db PATH`: es una llamada
directa real y puede tener efectos. La base alternativa solo aísla registros
personales, no archivos ni aplicaciones del equipo.

En KIRA: consultar Descargas y pedir mover un resultado; asignar una ficha y
corregir el tenedor; registrar ingreso/gasto, corregir, borrar y deshacer; abrir
un chat por nombre exacto y continuar con un segundo mensaje autorizado; pedir
música y completar solo el artista. Confirmar estado real fuera de la burbuja.

## Límites que impiden declarar la tanda completa

No se certificaron apps, recordatorios ni WhatsApp contra servicios reales.
La UI anuncia además herramientas inline/plugins que no convergen todavía en
el registro textual. Persisten adaptadores deterministas y referencias de
compatibilidad separadas. Solo una conversación se evaluó con proveedor real;
los escenarios grabados no equivalen a una evaluación amplia de generalización.
Estos puntos deben cerrarse antes de declarar cumplidos todos los criterios del
encargo; el resultado actual es una corrección auditada, no una certificación total.
