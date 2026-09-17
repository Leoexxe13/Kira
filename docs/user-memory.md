# Memoria por propietario y sesión

## Auditoría y ruta real

Antes: `memory/memory_manager.py` leía `memory/long_term.json` global; `OperationalContext` era RAM y `context_for` se indexaba únicamente por base personal. No había identidad ni recuperación del contexto textual tras reiniciar.

Ahora Chat/Home (`main.py::dispatch_user_request`), Remote autenticado por emparejamiento (`dashboard/server.py` → main.py), HUB y terminal convergen en Dispatcher. Al construirlo se resuelve un propietario confiable (`principal`) y una sesión (`session_id`). El propietario por defecto es la cuenta del sistema operativo, convertida en un UUID estable almacenado en SQLite. Desktop y el Remote emparejado comparten propietario y sesión `desktop`. No se aceptan identidades del texto ni de argumentos del modelo. El constructor permite vincular otros principals desde un backend de autenticación confiable; **no se ha creado un sistema de login multiusuario**.

## Persistencia y aislamiento

`memory/user_memory.py` usa las transacciones del PersonalStore existente. Migración aditiva e idempotente: tablas `memory_profiles`, `memory_sessions`, `memory_facts`, `memory_messages`, `memory_revisions`. No cambia ni migra Trabajo/Billetera ni su versión de esquema. Los hechos tienen UUID, propietario, tipo, clave, contenido, origen, creación/actualización y caducidad. Una clave por usuario y categoría se actualiza mediante UPSERT. Las conversaciones y pendientes se separan por usuario/sesión. Los datos de negocio mantienen sus services originales; esta modificación no convierte esos services en una aplicación multiusuario.

El JSON legado se conserva intacto y **no se importa automáticamente**, porque no identifica a su propietario. Las lecturas de memory_manager dentro del dispatcher se dirigen a SQLite mediante ContextVar. Las escrituras globales dentro de peticiones vinculadas se rechazan: las nuevas escrituras pasan por la herramienta con confirmación. El código legado de voz fuera del dispatcher no se modifica.

`user_memory` es una herramienta del registro real para consultar/guardar/corregir/olvidar. No acepta user_id. Las escrituras necesitan confirmación del Core. Las credenciales reconocibles por etiqueta o formato se rechazan/redactan; esto no puede identificar de forma infalible un secreto arbitrario sin etiqueta. Los mensajes crudos se conservan por usuario; olvidar un recuerdo no promete borrarlos. Al corregir/olvidar se invalidan resúmenes y el contexto conversacional derivado mediante revisión por usuario para no reintroducir el dato anterior en otra sesión activa.

## Recuperación y presupuesto

Cada turno carga hasta 2.500 caracteres de recuerdos pertinentes y preferencias mediante la búsqueda léxica ya existente; conserva el límite de ocho mensajes recientes de OperationalContext. Los datos son contexto no confiable, no instrucciones de sistema. Todos los providers reciben ese mismo contexto desde SemanticInterpreter.

El estado se guarda al terminar cada turno, también tras fallo del proveedor. Al reconstruir Dispatcher se restaura pending_operation con el normalizador canónico. Las confirmaciones restauradas se vencen explícitamente; restaurar no invoca tools. Los paths se revalidan para reconstruir referencias de ResourceResolver. Los resultados restantes vuelven a validarse por las tools al ejecutarse.

## Validación

`tests/test_user_memory.py` usa SQLite temporal real, servicios y Tool Registry; solo la respuesta del modelo se registra como fixture. Prueba reinicio, separación A/B, separación de sesiones, corrección/olvido/caducidad, memoria compartida entre sesiones, fallo y cambio de proveedor, confirmación de escritura real, limpieza del pendiente completado, restauración sin ejecución, invalidación de contexto derivado y redacción de credenciales reconocibles.

No se probó un modelo local vivo ni un login externo. Para cambiar propietario en una aplicación autenticada, el servidor debe construir/seleccionar el dispatcher con el principal validado; nunca debe copiar un user_id del cuerpo de una petición remota.
