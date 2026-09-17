# KIRA: runtime semántico de texto

Estado posterior y límites verificados: [auditoría del 16 de septiembre](core-audit-2026-09-16.md).
Ese reporte sustituye las cifras y conclusiones de validación de este documento histórico.

## Auditoría (rama codex/kira-next)

* CHAT: `MainWindow._send` → callback `JarvisLive._route_text_command_v1` → worker → `dispatch_user_request`.
* HOME: `_send_home_command` → `_send(source='home')` → misma ruta.
* Remote: callback del dashboard → `asyncio.to_thread(self.dispatch_user_request, text, 'remote')`.
* HUB: `PersonalPage` antes construía otro Dispatcher; ahora `shared_dispatcher` devuelve la misma instancia por ruta de SQLite.
* Tools Live: se conservan; el modo de voz está desactivado por defecto en `runtime_config`. Esta tanda no cambia audio.
* `_route_text_command_local` conserva compatibilidad antigua, pero el worker textual ya no lo utiliza como fallback.
* No se encontró una clase activa `GoalReasoningAgent` en los archivos Python inspeccionados. No se ejecutaron scripts históricos.

## Causas corregidas

El dispatcher anterior solo invocaba IA si encontraba verbos de una lista. Su tabla ROUTES duplicaba un subconjunto del registro. El chatbot final recibía únicamente el mensaje actual y una instrucción de que no tenía herramientas. No recibía conversación ni pendientes. Una respuesta incompleta del adaptador local impedía probar el siguiente proveedor. El cliente local limitaba la salida a 150 tokens. Los resultados de archivos eran prosa y no alimentaban referencias reutilizables.

Se retiraron las condiciones léxicas de selección de IA, la tabla ROUTES, la división de peticiones por verbos concretos y los parches de música/notificaciones/movimiento masivo añadidos para capturas individuales. Los parsers locales existentes quedan como compatibilidad determinista; no son el fallback de lenguaje desconocido.

## Ruta actual

Entradas → instancia compartida → contexto → Fast Path existente cuando resuelve → SemanticInterpreter → propuesta JSON → validación de capacidades/esquema/referencias → confirmación según metadata de la tool → ActionRegistry → evidencia → contexto/evento UI/respuesta.

`ProviderManager.interpret_request` utiliza el proveedor textual existente (Groq configurado) y después el cliente local existente, restringido a localhost y sin iniciar servidores automáticamente. No usa Gemini Live. Offline se omite la petición online; las acciones deterministas siguen disponibles y el lenguaje nuevo necesita un modelo local funcionando. Un proveedor caído produce estado pendiente, nunca éxito ficticio.

`OperationalContext` mantiene conversación acotada, plan, resultados, recursos, operación fallida y aclaraciones. El facade CONTEXT dirige las herramientas al contexto del dispatcher mediante ContextVar. Las referencias de los parsers personales anteriores siguen en el Context de la misma base; el dispatcher publica sus últimos registros al contexto semántico. No se envía el ledger completo: resultados recientes limitados y metadatos; sí se envían las frases recientes y los registros necesarios para resolver la petición.

## Capacidades y seguridad

Las descripciones y esquemas salen de `ActionRegistry.semantic_capabilities`, exclusivamente tools válidas registradas. Los adaptadores internos de compatibilidad no se anuncian al modelo. `personal_records` expone los mismos Wallet/Work/PersonalEdits usados por la UI; no hay SQL generado por IA. El esquema SQLite no cambia.

Cada paso valida tipos/campos; las referencias solo leen contexto o pasos ya ejecutados. foreach queda limitado a 50 elementos y el plan a 8 pasos. Tools sin metadata de seguridad requieren confirmación. Confirmaciones duran 120 segundos y comparan huellas de archivos/registros para rechazar cambios posteriores. La tool WhatsApp sigue siendo dueña de la resolución y guardas de destinatario. No se envió ningún mensaje en las pruebas.

Files devuelve observaciones reales. Wallet/work releen SQLite. Una tool antigua que solo devuelve prosa queda como ejecutada, sin promoverse automáticamente a verificada. Los errores interrumpen la continuación, conservan el fallo y tienen límite de reintentos idénticos. Las llamadas de un plan no forman una transacción global: si un paso posterior falla, se informa lo ya ejecutado.

## Inspección y validación

`python scripts/test_kira_dispatch.py --interactive --db /tmp/kira-prueba.db` utiliza el dispatcher real y conserva contexto. Puede modificar archivos reales si se le solicita: la base alternativa solo aísla los registros personales.

`--dry-run` llama al mismo intérprete y valida el plan sin ejecutar tools. Muestra contexto usado, proveedor, plan, tools, ejecución, verificación, contexto actualizado y respuesta. No registra estos datos sensibles por defecto en la UI.

`tests/test_semantic_runtime.py` sustituye solo la frontera del modelo con respuestas grabadas. Registro, validación, archivos y SQLite son reales/temporales. Esto verifica integración y seguridad, no calidad lingüística del modelo. La prueba live adicional fue de interpretación, sin herramientas: Groq produjo un plan válido para una paráfrasis nueva de listar Descargas. Ollama no respondió en el entorno comprobado. La generalización amplia y las acciones reales de macOS/WhatsApp requieren validación manual.
