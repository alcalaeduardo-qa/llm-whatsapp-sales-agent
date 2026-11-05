# Avances de Sofía — documento de traspaso

> **Para Claude en un chat nuevo:** lee este archivo completo y después `plan-desarrollo-sofia.md` (el plan original de 10 días). Aquí está lo que ya se hizo, las decisiones de Eduardo que cambiaron el plan, cómo se opera el proyecto y qué sigue. Antes de tocar código corre `git status`, `git branch --show-current` y `ls prompts/` (debe existir `sofia.md`; ver "Estado del repositorio").

Última actualización: 4 de noviembre de 2026.

---

## 1. Qué es

**Sofía** es una asistente de ventas por WhatsApp para **Sanando el Alma** (sesiones de acompañamiento emocional de Eduardo, por escrito y audios en vivo, $300 MXN la hora). Responde con Claude (`claude-haiku-5-5`), guarda la conversación en Supabase y avisa a Eduardo por Telegram. Objetivo doble: producción real y pieza de portafolio de AI/Eval Engineer.

Repositorio público: `github.com/alcalaeduardo-qa/llm-whatsapp-sales-agent`.

---

## 2. Estado por día

| Día | Tema | Estado |
|---|---|---|
| 1 | Hotfix + estructura modular | ✅ en `main` (PR #1) |
| 2 | Tests base (pytest, ruff, pre-commit, respx, freezegun) | ✅ en `main` (PRs #2 y #4) |
| 3 | Guardrails: crisis antes del LLM + validador de salida | ✅ en `main` (PRs #3 y #4) |
| 4 | Derivación a Eduardo, modo humano, comandos de Telegram | ✅ en `main` (PRs #5 y #6), **probado en vivo** |
| 5 | Agenda real con iCloud | ✅ en `main` (PR #7), probado en vivo |
| 6 | Audio (transcripción con Groq) | ✅ en `main` (PR #8), probado en vivo |
| 7–8 | Evals + red-teaming | ✅ en `main` (PR #9): 64 casos, 89.1 % pasan (línea base 32.8 %) |
| 9 | Docker, CI, deploy en Railway, métricas, retención 90 días | 🟡 Todo en `main` (PRs #11–#14) y **en producción en Railway** desde el 4 de noviembre; falta el subdominio |
| 10 | README bilingüe, demo, PRIVACY, LinkedIn | ⬜ |

Tests: **448 pasan, cobertura 98 %** (mínimo exigido 80 %).

---

## 3. Estado del repositorio (al cerrar este chat)

- Rama actual: **`dia-9-railway`** (desde `main` con el PR #14 integrado).
- Rama vieja `dia-4-derivacion` (local y en GitHub) ya está integrada en `main`; falta borrarla (Eduardo debe autorizarlo).
- `prompts/sofia.md` (prompt real de venta) **no se versiona** (`.gitignore`); en el repo va `prompts/sofia.example.md`. La app usa el real si existe y si no la plantilla (con aviso en el log). Sigue visible en commits viejos (Eduardo decidió no reescribir el historial).
  - ⚠️ Al cambiar a una rama/commit antiguo donde `sofia.md` estaba versionado, git lo borra al volver. Pasó una vez y se recuperó con `git show 49578b0:prompts/sofia.md > prompts/sofia.md`. **Conviene que Eduardo guarde una copia fuera del repo.**

---

## 4. Arquitectura

```
WhatsApp ──► POST /webhook (firma HMAC de Meta) ──► 200 inmediato + BackgroundTask
                 │
                 ├─ texto ─► procesar_mensaje
                 │            1. registrar en Supabase (idempotente por wa_message_id)
                 │            2. si modo humano → reenviar a Telegram (crisis explícita sí recibe la Línea de la Vida)
                 │            3. si venta en pausa por crisis → mensaje de contención
                 │            4. detectar crisis (palabras explícitas → si no, clasificador Haiku) → respuesta fija + pausa + alerta
                 │            5. generar con Claude (tool derivar_a_eduardo) → validar → 1 reintento → respaldo
                 │            6. enviar por WhatsApp; si derivó → modo humano + alerta 🙋
                 ├─ nota de voz ─► descargar → transcribir con Groq → procesar_mensaje("[nota de voz] …")
                 │                (si falla: "¿me lo puedes escribir?")
                 └─ imagen/documento ─► reenviar a Telegram + derivar (motivo comprobante)

Telegram (Eduardo) ──► POST /telegram/webhook (secret token) ──► comandos.manejar
```

| Archivo | Qué hace |
|---|---|
| `app/main.py` | FastAPI: webhooks de WhatsApp y Telegram, flujo de mensajes, crisis, derivación, comprobantes |
| `app/llm.py` | Llamadas a Claude: respuesta (con tool `derivar_a_eduardo`, `effort` low) y clasificador de crisis (sin thinking) |
| `app/guardrails.py` | Detección de crisis, validador de salida, respuestas fijas (crisis, pausa, derivación, comprobante, reserva, respaldo) |
| `app/prompt.py` | Carga `prompts/sofia.md` (o la plantilla) y sustituye `{{variables}}` |
| `app/scheduling.py` | Horarios ofrecidos con día y fecha en español; salta los ocupados |
| `app/transcripcion.py` | **Día 6**: notas de voz con Groq (`whisper-large-v3-turbo`, español) |
| `evals/` | **Días 7–8**: `run.py` (runner con DeepEval), `metricas.py` (22 checks + 2 jueces), `simulador.py` (flujo real sin WhatsApp), `dataset/` (48 ventas + 16 red-team), `clasificador.py` + `casos_crisis.yaml`, `RESULTS.md`, `historial.json` |
| `docs/FMEA.md` | 24 modos de falla con S/O/D, RPN, control y métrica |
| `app/calendario.py` | **Día 5**: iCloud por CalDAV: lee ocupados (caché 2 min) y crea la reserva |
| `app/comandos.py` | Comandos de Eduardo en Telegram |
| `app/metricas.py` | **Día 9**: latencia, tokens, costo y fase por respuesta, sin teléfono ni contenido |
| `app/store.py` | Supabase: `mensajes` y `conversaciones` (estado) |
| `app/telegram.py` | Alertas, reenvío de archivos, registro del webhook (`python -m app.telegram <url>`) |
| `app/whatsapp.py` | Firma de Meta, parseo, envío, descarga de media |
| `db/schema.sql` | Esquema (idempotente) |
| `tests/` | `falsos.py` tiene los dobles compartidos (store, Claude con httpx2, fixture `entorno`) |

### Base de datos (Supabase)
- `mensajes`: `telefono, rol (user|assistant), contenido, wa_message_id (único), creado_en`. Los mensajes que Eduardo manda desde Telegram se guardan con prefijo `[Eduardo] `.
- `conversaciones`: `telefono (PK), venta_pausada, motivo_pausa, pausada_en, humano_activo, motivo_humano, humano_desde, actualizado_en`.
- Migraciones aplicadas en producción: `dia3_conversaciones`, `dia4_modo_humano`, `dia5_recordatorios` (tabla `recordatorios`: evento_uid PK, telefono, inicio, enviado_en), `dia9_retencion_90_dias` (función `purgar_inactivos(dias)` + tarea de `pg_cron` `sofia-retencion-90-dias`, diaria a las 9:00 UTC), `dia9_metricas_aviso` (tabla `metricas` y columna `conversaciones.aviso_privacidad_en`).
- Las migraciones van **antes** del código que las usa: sin `aviso_privacidad_en` falla la lectura del estado y Sofía respondería en modo humano.

### Comandos de Telegram (solo desde `TELEGRAM_CHAT_ID`)
- Contestar (reply) a una alerta → el texto le llega a la persona por WhatsApp y Sofía queda en silencio.
- `/responder <tel> <mensaje>` (teléfono sin espacios)
- `/reanudar <tel>` → Sofía vuelve a atender (quita modo humano y pausa por crisis)
- `/pausar <tel>` → modo humano manual
- `/reservar <tel> <día/mes> <hora>` o, contestando a la alerta, `/reservar 2/11 14` (**Día 5**). Acepta `hoy`, `mañana`, `2pm`, `14:00`.
- `/ayuda`

---

## 5. Decisiones de Eduardo (mandan sobre el plan original)

- Horario: sesiones de **1 hora**, inicios de **2 a 7 PM hora México, los 7 días** (fines de semana incluidos, a propósito).
- **Nunca se aparta un horario sin pago.** Flujo: Sofía ofrece horarios libres → la persona paga y manda comprobante → derivación → Eduardo valida → **Eduardo ordena `/reservar`** → Sofía crea el evento y confirma por WhatsApp. La IA nunca reserva por su cuenta (se descartó la tool `apartar_horario` y `/confirmar` del plan).
- Calendario: el del iPhone, **iCloud, calendario "Casa"** (no Google Calendar). **Solo "Casa"** bloquea horarios; los otros calendarios de iCloud ("Trabajo", "Calendario") se ignoran a propósito.
- **Recordatorios (pendiente de implementar):**
  - A la persona: WhatsApp **10 minutos antes** de la sesión.
  - A Eduardo: alertas del calendario **10 minutos antes y a la hora** de la sesión (VALARM en el evento que crea `/reservar`).
- Eduardo responde a las personas **desde Telegram** (sale por el número de Sofía).
- Alertas de crisis por Telegram.
- Prompt real fuera del repo (público).
- Commits solo con el correo **alcala.edugar@gmail.com** (configurado en el repo).

---

## 6. Operación local

```powershell
cd C:\Users\Admin\Desktop\claude\sofia
.venv\Scripts\python.exe -m uvicorn app.main:app --port 80 --reload --reload-include .env   # ngrok → puerto 80; se reinicia solo al cambiar código o .env (solo en desarrollo)
.venv\Scripts\python.exe -m pytest                             # tests + cobertura
.venv\Scripts\python.exe -m app.telegram https://<url-ngrok>   # registrar webhook de Telegram (si cambia la URL)
```

- ngrok: dominio fijo gratuito `https://spore-departure-chloride.ngrok-free.dev` → `localhost:80`. Inspector en `http://127.0.0.1:4040` (sirve para ver qué llegó y con qué código respondió).
- Meta: webhook en `https://<ngrok>/webhook`. Número de **prueba** de Meta (+1 555-651-2097): solo puede escribir a números de la lista "Para" en *API Setup*. Token de **usuario del sistema `sofia-v2`, no caduca**, app `sofia-v2`.
- Con `--reload --reload-include .env` (requiere `watchfiles`, en `requirements-dev.txt`) uvicorn se reinicia solo al cambiar código o `.env`. El prompt se lee en cada mensaje. En producción se arranca sin `--reload`.

### Variables de `.env` (ver `.env.example`)
Meta (`VERIFY_TOKEN`, `APP_SECRET`, `WHATSAPP_TOKEN`, `PHONE_NUMBER_ID`), `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `MAX_TOKENS=16000`, `SOFIA_EFFORT=low`, Supabase (`SUPABASE_URL`, `SUPABASE_KEY` = service_role), horario, datos de pago, guardrails, Telegram (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_SECRET`) y, nuevo en el Día 5, `ICLOUD_USUARIO`, `ICLOUD_PASSWORD_APP` (contraseña de app de appleid.apple.com), `ICLOUD_CALENDARIO=Casa`, `SESION_MINUTOS=60`.

---

## 7. Cómo trabajamos (reglas acordadas)

- **Una rama por día desde `main` actualizado y PR directo contra `main`.** No apilar PRs (cuando se apilaron, los merges quedaron en ramas intermedias).
- Eduardo aprueba cada commit, PR, migración de Supabase y borrado; los merges los hace él en GitHub.
- Al terminar cada día: marcar las casillas en `plan-desarrollo-sofia.md`.
- Nunca pedir ni pegar secretos en el chat: Eduardo los pone directo en `.env`.
- Probar en vivo después de los tests: varios bugs solo aparecieron así.

---

## 8. Trampas ya descubiertas (no repetir)

- **`claude-haiku-5-5` razona antes de responder y eso cuenta contra `max_tokens`.** Con 800 tokens salían respuestas vacías; con `max_tokens=5` el clasificador nunca decía "SI". Ahora: respuestas con `max_tokens=16000` + `output_config={"effort": "low"}`; clasificador con `thinking={"type": "disabled"}`, `max_tokens=256` y falla segura si no hay veredicto.
- No usar la variable `CLAUDE_EFFORT`: Claude Code la define en su entorno. La nuestra es `SOFIA_EFFORT`.
- El SDK `anthropic` 2.x usa **`httpx2`**: respx no lo intercepta. En tests Claude se mockea con `httpx2.MockTransport` (ver `tests/falsos.py`).
- `httpx` registra URLs completas en INFO; la de Telegram lleva el token → el logger `httpx` está en WARNING.
- Windows formatea fechas en inglés: los días y meses se arman a mano (`scheduling.dia_y_fecha`), y las opciones de horario llevan día y fecha explícitos para que Claude no deduzca.
- Tras `/reanudar`, Claude volvía a derivar al leer "quiero hablar con Eduardo" en el historial → mensajes de Eduardo con prefijo `[Eduardo]` y nota `NOTA_DEVUELTA` en el prompt.
- En Git Bash, los heredocs con `\n` dentro de scripts de Python se convirtieron en saltos reales: para editar código usar el editor, no `sed`/heredocs con escapes. Los archivos deben quedar con fin de línea LF.
- **Ventana de 24 h de WhatsApp:** fuera de ella Meta **acepta** el texto libre (200) y segundos después manda un estado `failed` con código **131047** por el webhook. Por eso: antes de mandar avisos de reserva o recordatorios se revisa el último mensaje de la persona (`store.en_ventana_de_whatsapp`), y cualquier estado `failed` se le reporta a Eduardo por Telegram (`main.avisar_fallos`).
- Claude solo ve el historial de mensajes: no sabía qué sesiones estaban reservadas y llegó a decirle a una persona con reserva que "su lugar no estaba apartado". Ahora `main.contexto_de` le pasa las sesiones próximas de esa persona leídas del calendario (`NOTA_SESION_RESERVADA`), junto con `NOTA_DEVUELTA` si Eduardo ya la atendió.
- **Evals:** un juez LLM que no conoce la especificación castiga al agente por seguirla. El juez recibe el prompt real en `scenario` (solo en ejecución; nunca se guarda). `ConversationalGEval` de DeepEval 4.3 **no** acepta `CONTEXT` ni `CHATBOT_ROLE` como parámetros: solo ROLE, CONTENT, SCENARIO, EXPECTED_OUTCOME, METADATA, TAGS, TOOLS_CALLED, RETRIEVAL_CONTEXT y AUDIO.
- **Clasificador de crisis:** con etiquetas SI/NO, el modelo contestaba la pregunta de la persona ("¿hablo con una IA?" → "Sí") y contaba como crisis. Ahora usa RIESGO/NORMAL y recibe los mensajes en `<mensajes>`.
- **"juntos":** rechazarlo en el validador hacía que Claude lo repitiera y la conversación terminara en el respaldo. Se reemplaza por "en conjunto" en `limpiar_texto`. El largo es regla suave (se envía si es la única falla).
- Los tests tienen un candado (`tests/conftest.py`): si un test intenta usar el Supabase, el Claude o el iCloud reales, falla. Cada función nueva de `store` debe tener su doble en `StoreFalso`.
- Meta manda los móviles de México como `521…`; para enviar se usa `52…` (`normalizar_numero`). En comandos, `numero_meta` hace lo inverso.

---

## 9. Qué falta (en orden)

### Cierre del Día 5
Probado en vivo el 3 de noviembre: lectura de "Casa", `/reservar` (evento con alertas + confirmación por WhatsApp entregada), ventana de 24 h (fallo 131047 detectado y reportado) y nota de sesión reservada para Claude.
1. ~~Merge del PR #7~~ y ramas borradas (3 de noviembre).
2. **Recordatorio por WhatsApp en vivo:** quedaron dos sesiones de prueba el 3/11 a las 3 y 4 PM; con el servidor encendido llegan a las 2:50 y 3:50. Confirmar que llegó y borrar los eventos.
3. **Plantilla de WhatsApp** (opcional, recomendada antes de producción) para recordatorios fuera de la ventana de 24 h: WhatsApp Manager, categoría Utilidad, español (México), cuerpo con `{{1}}` = la hora; poner su nombre en `WHATSAPP_PLANTILLA_RECORDATORIO`.

### Día 6 (notas de voz)
Probado en vivo el 3 de noviembre: la transcripción fue exacta y Sofía respondió como a un texto (unos 20 s en total). La transcripción entra al flujo normal con el prefijo `[nota de voz]` (crisis, modo humano y sesiones reservadas aplican igual); Claude recibe `NOTA_AUDIO`. PR #8 abierto.
- Decidido (3 de noviembre): Sofía **sí** puede vender una sesión adicional a quien ya tiene una, pero siempre pago primero y comprobante a Eduardo, que confirma con `/reservar` (`NOTA_SESION_RESERVADA`).

### Días 7–8 (evals y red-teaming)
Hechos el 3 de noviembre con **DeepEval** (Opus 5.5 como juez) contra el prompt real; datos de pago ficticios en los evals. Comandos:
- `python -m evals.run` (completo, ~2.5 USD), `--sin-juez` (centavos), `--solo <categoría|id|ventas|redteam>`, `--limite N`.
- `python -m evals.clasificador` (sensibilidad/especificidad del clasificador de crisis, centavos).

Resultados: 32.8 % → 46.9 % → 57.8 % → **89.1 %** de casos pasan; red-team 94 %; clasificador 27/27 crisis reales y 60/60 casos sin riesgo. Gasto total en evals: unos 7.8 USD. Arreglos en Sofía que salieron de los evals: "juntos" → "en conjunto", clasificador RIESGO/NORMAL, derivación si Sofía la promete, largo como regla suave, límite de 1000 caracteres, segunda sesión con pago primero.

Pendiente del Día 7:
1. ~~PR #9~~ fusionado (3 de noviembre). README bilingüe agregado (rama `docs-readme`).
2. Hallazgos abiertos (ver `docs/FMEA.md`): hora equivalente mal calculada para otros países (prioridad 1: calcularla en código), "estás lista", enlace de pago antes de elegir horario, no aceptar un horario válido propuesto, guía de apego no ofrecida a alguien de EE. UU.
3. Agregar casos de notas de voz al dataset.

### Día 9 (en curso)
- **CI** (`.github/workflows/ci.yml`): en cada PR y push a `main`, ruff + 389 tests (sin secretos) y evals sin juez (`--minimo 85`, ~0.11 USD). `evals-juez.yml`: corrida completa con juez solo a mano (Actions → Run workflow, ~2.5 USD).
- **Secretos que Eduardo debe crear en GitHub** (Settings → Secrets and variables → Actions): `ANTHROPIC_API_KEY` y `SOFIA_PROMPT` (contenido completo de `prompts/sofia.md`). Sin ellos los evals se omiten o usan la plantilla pública con un aviso.
- **Hora equivalente** en código (`scheduling.horas_equivalentes`, 11 zonas con `zoneinfo`); Claude la recibe en `NOTA_HORAS_EQUIVALENTES`. Verificado: Houston 3 PM = 3 PM México en noviembre, España +7 h.
- **Docker**: `Dockerfile` multi-etapa (usuario sin privilegios, healthcheck, `$PORT`), `.dockerignore` (excluye `.env` y `prompts/sofia.md`), `docker-compose.yml`. El CI construye la imagen, verifica que no contenga el prompt y que responda `/health`. En el servidor el prompt va en la variable `SOFIA_PROMPT` (`prompt.texto_del_prompt`).
- **BORRAR MIS DATOS** (`guardrails.es_borrar_mis_datos`, `main.borrar_datos`, `store.borrar_datos`): frase exacta escrita o en nota de voz; borra mensajes, estado y recordatorios, confirma por WhatsApp y avisa a Eduardo. El evento del calendario no se toca.
- **Retención de 90 días**: función de Postgres `purgar_inactivos(90)` programada con `pg_cron` (no depende del servidor). Probada en una transacción no confirmada: borró al inactivo y no tocó al reciente ni los datos reales.
- **Métricas** (`app/metricas.py`, tabla `metricas`): una fila por respuesta de Claude o de crisis, con latencia hasta el envío, tokens y costo sumando clasificador y reintentos. Sin teléfono ni contenido, por eso no las tocan el borrado ni la retención. La fase sale del texto enviado sin otra llamada a Claude: `conversacion` agrupa filtro, patrón y objeción. Las consultas están en `docs/DEPLOY.md`.
- **Aviso de privacidad**: pie fijo en la primera respuesta de Sofía, con enlace (`URL_PRIVACIDAD`) y BORRAR MIS DATOS. No entra al historial para que Claude no lo copie. La respuesta de crisis no lo lleva; llega cuando Sofía retoma. `PRIVACY.md` ya describe proveedores, retención y borrado, pero **no tuvo revisión legal**.
- **Railway (producción desde el 4 de noviembre):** proyecto `valiant-vitality`, servicio `llm-whatsapp-sales-agent`, plan Trial, URL `https://llm-whatsapp-sales-agent-production.up.railway.app`. Los webhooks de Meta y Telegram ya apuntan ahí, así que ngrok y uvicorn local deben seguir apagados salvo para volver atrás. Probado en vivo: texto, nota de voz, comando de Telegram y métricas. Unos 6.5 s por respuesta y entre 0.0007 y 0.001 USD.
- **Hallazgos del deploy:** el log de acceso de uvicorn guardó `VERIFY_TOKEN` al verificar el webhook de Meta (va en la URL). Ya se quitó ese log en el `Dockerfile`; **Eduardo debe cambiar el token en Railway y en Meta después de publicar ese cambio**. También se silenció el logger `quic` y se borró `railway.toml`, que Railway no usa: `/health` se configuró a mano y Auto deploy está apagado.
- Falta: subdominio (`sanandoalmaa.com` no tenía registros DNS el 4 de noviembre, hay que activar el dominio primero) y elegir plan de Railway antes de que se acabe el Trial.

### Días 9–10
Seguir `plan-desarrollo-sofia.md`. Notas para cuando lleguen:
- Día 9 (CI): correr `pytest` y `python -m evals.run --sin-juez` en cada PR. Los evals sin juez llaman a Claude (necesitan `ANTHROPIC_API_KEY` como secreto) y usan el prompt de ejemplo si el real no está.
- Día 9 (deploy): `prompts/sofia.md` no viene en el repo → proveerlo en el servidor (archivo secreto o variable). El webhook de Telegram y el de Meta cambian a la URL definitiva.
- Día 10 (PRIVACY): mencionar que Telegram recibe mensajes y comprobantes para Eduardo, que iCloud guarda el teléfono en el evento y que Groq recibe las notas de voz para transcribirlas.

### Pendientes menores
- Probar en vivo comprobante (foto) y crisis por WhatsApp (cubiertos por tests, no probados en vivo).
- Pasar del número de prueba de Meta a un número real (nombre visible aprobado, método de pago; la verificación del negocio no es obligatoria para empezar).
