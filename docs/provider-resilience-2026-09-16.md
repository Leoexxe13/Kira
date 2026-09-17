# Resiliencia del intérprete textual

El orden real es: operación local de alta confianza → Groq textual → Ollama/local → Gemini textual → aclaración honesta. Gemini Live no participa.

`ProviderManager` conserva por proveedor `AVAILABLE`, `DEGRADED`, `RATE_LIMITED`, `UNAVAILABLE` o `DISCONNECTED`, junto con último éxito/error, fallos consecutivos, cooldown y latencia. Groq 429 usa cooldown de 30 s; Gemini degradado, 30 s; el servidor local desconectado, 10 s. El estado se consulta con `provider_health()` y `status()`.

La configuración de este entorno no contiene `llm_provider`, `llm_url` ni `llm_model`; por eso el cliente local usa sus valores seguros predeterminados: Ollama en `http://localhost:11434`, modelo `llama3.2`. La comprobación local no encontró un ejecutable `ollama` en `PATH` y las llamadas existentes devolvieron desconexión. KIRA no inicia procesos durante esta tanda ni hace depender el arranque de Ollama. Para activarlo, el servicio debe estar iniciado por el usuario y el modelo descargado.

La causa concreta de la desconexión local es `core/llm_client.py`: la petición HTTP a Ollama lanza `requests.exceptions.ConnectionError` y `call_llm(..., allow_restart=False)` la envuelve en `RuntimeError('El modelo local no está disponible')` (línea 318). `ProviderManager` conserva ahora el traceback con `logger.exception`, clasifica el proveedor como `DISCONNECTED` y respeta el cooldown; no se reintenta esa conexión en cada turno.

El intérprete online no recibe el historial completo. Si Groq responde 429, se registra el código en logs y se salta durante su cooldown. Si Ollama falla, se intenta Gemini textual una sola vez. Si todos fallan, la interfaz muestra únicamente: “Los modelos de interpretación están temporalmente no disponibles. Las funciones locales siguen activas; conservé el contexto y no ejecuté cambios.” El detalle técnico permanece en `semantic.trace['provider_error']` y en el log.

Las operaciones de apertura de aplicaciones, registros personales directos, tareas y herramientas deterministas no necesitan un LLM. Las peticiones locales claras siguen usando el registro y sus servicios; las consultas semánticas nuevas requieren un proveedor disponible.

`pending_operation` usa un único esquema canónico (`goal`, `plan`, `calls`, `missing_fields`, `candidates`, `original_text`, `created_at`, `source`, `outputs`, `step_index`, `fingerprints`, `kind`). Los estados antiguos se normalizan en memoria; se conservan campos extra de herramientas como acción, mensaje y destinatario.

Las pruebas de resiliencia cubren selección, 429/cooldown, fallback local→Gemini, 503, recuperación y conservación de pendientes. La suite completa quedó en 190 tests: 189 aprobados y 1 evaluación online omitida por defecto. No se probaron envíos reales de WhatsApp ni se modificó voz.
