# WhatsApp: capacidad y memoria de resolución

La herramienta sigue siendo `actions/whatsapp_web.py`, con su worker, WA-JS,
Chromium y perfil persistente existentes. No cambia el scoring/ranking.

## Intención y propiedad del recurso

Gemini Live sigue interpretando el lenguaje y seleccionando las herramientas.
El prompt y el esquema distinguen acceso simple (`open`) de operaciones internas.
`core/capabilities.py` declara la propiedad del recurso, no interpreta frases.
Fast Path cede los textos que mencionan ese recurso; el registro y las entradas
genéricas de apps/navegador/mensajería impiden apropiárselo.

Una llamada estructurada `open_app(app_name="WhatsApp")` representa acceso simple
y el registro la deriva al backend especializado. Si el objetivo incluye una
operación, se bloquea la ruta genérica y se pide usar la herramienta apropiada,
sin descartar argumentos para convertirla en una simple apertura. Si la
herramienta especializada no está disponible, no hay fallback nativo.

Fuera de un estado pendiente, el router conserva la política FREE-FIRST de
`fe42585`: conversación general suficientemente larga va a Groq, las señales
existentes de herramientas/datos actuales van a Gemini, y `/groq`/`/gemini`
conservan sus controles explícitos. No se añadió una lista de frases nueva.
Cuando existe estado pendiente, la ruta lo envía a Gemini Live para que el
resolver semántico continúe la operación; esto es una excepción basada en
estado, no en el texto de la respuesta.

## Estado pendiente

El worker publica mediante `TOOL.pending_context` una copia protegida por lock
de la consulta, candidatos y operación original, incluido el mensaje pendiente.
El registro expone ese estado al router sin acceder a Playwright desde otro hilo.
Durante los 300 segundos existentes de selección, la nueva entrada se entrega
a Gemini con ese contexto, cualquiera que sea su formulación. El modelo decide
si es elección, corrección o cambio de tema; el router no interpreta ordinales.
La elección sigue limitada a los IDs/índices de la última lista.

El estado se limpia al elegir, cerrar, perder autenticación o cambiar de cuenta.
La expiración impide tanto exponer el contexto como aceptar una selección vieja.
Un cambio de tema no autoriza ejecutar la operación pendiente; esa decisión
semántica permanece en Gemini. La ruta de voz ya usa Gemini directamente.

## Persistencia técnica dentro de memory_manager

`memory/memory_manager.py` administra `memory/whatsapp_contacts.json`, excluido
de Git. Tiene versión y un mapa `accounts[cuenta][alias]` con `id`, `name` y
`confirmed_at`. Usa el lock existente y reemplazo atómico con archivo temporal
privado en el mismo directorio. Un archivo corrupto no se considera memoria vacía
ni se sobrescribe silenciosamente.

El alias usa `_norm` (acentos, mayúsculas, espacios); no usa la equivalencia fuzzy
Y/I. Solo una elección explícita de candidatos, seguida de las dos verificaciones
de ID actuales y la comprobación de cuenta, escribe una asociación. El alias es
la consulta original, no el texto de la respuesta contextual. Ranking, búsqueda
y apertura automática nunca aprenden. Un fallo de escritura se comunica mediante
`memory_warning` sin afirmar que la asociación quedó guardada.

La consulta futura busca la asociación exacta en la cuenta actual y valida el ID
mediante `WPP.chat.get` / `WPP.contact.get`, sin limitarse a los 250 chats recientes.
Después ejecuta la apertura y verificaciones de ID existentes. `send` conserva
su comprobación final y verifica además que la cuenta no haya cambiado.
Si falla memoria/identidad, no hay ranking alternativo ni envío. Se puede intentar
de nuevo tras un fallo temporal o pedir `forget_contact` y resolver normalmente.
El olvido afecta solo al alias de la cuenta actual. Una asociación diferente no
puede sobrescribirse sin olvido previo.

`long_term.json`, sus categorías, recuerdos, recuperación bajo demanda y presupuesto
de prompt se conservan. El almacén técnico no se vuelca al prompt ni a recall.

## Identidad y límites

La instalación tiene memoria general compartida y un `user_name` de presentación;
no tiene un identificador seguro de usuarios de KIRA. Este bloque no introduce
multiusuario: aísla únicamente por la cuenta autenticada de WhatsApp, obtenida de
`WPP.conn.getMyUserId()` sin ID de dispositivo. Dos personas que usen la misma
cuenta WhatsApp comparten asociaciones. Si esa identidad no puede determinarse,
la resolución por nombre/candidato queda bloqueada; abrir, autenticarse y cerrar
siguen disponibles. El lock cubre hilos de un proceso, no instancias concurrentes.

La interpretación lingüística depende del modelo existente. Los tests aislados
comprueban el contrato usando un doble de la sesión semántica; no demuestran la
comprensión de un modelo real. No se abrió una cuenta real ni se envió un mensaje.
Se conservan las rutas de perfil y bundle WA-JS del commit de partida.

Referencias de las APIs: [identidad de cuenta](https://wppconnect.io/wa-js/functions/conn.getMyUserId.html),
[chat por ID](https://wppconnect.io/wa-js/functions/chat.get.html),
[contacto por ID](https://wppconnect.io/wa-js/functions/contact.get.html).

## Verificación aislada

```sh
python3 -B -m unittest discover -s tests -v
git diff --check
```

Los tests usan dobles de Playwright/WA-JS, no inician workers reales y crean
directorios temporales únicamente dentro del repositorio, eliminados al terminar.
