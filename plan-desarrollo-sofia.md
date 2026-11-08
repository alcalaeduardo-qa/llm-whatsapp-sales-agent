# Plan de desarrollo — llm-whatsapp-sales-agent (Sofía)

**Objetivo:** producción real para Sanando el Alma + pieza de portafolio AI/Eval Engineer.
**Ritmo:** 10 días hábiles × ~4h (≈40h). README bilingüe. Hoy corre local + ngrok.

## Decisiones
| Tema | Decisión |
|---|---|
| Horario de sesiones | 2–8 PM hora México (última sesión inicia 7 PM) |
| Transcripción | Groq `whisper-large-v3-turbo` (recomendado; ~$0.04 USD/h audio) |
| Deploy | Railway con Dockerfile (recomendado); Contabo cuando sea multi-tenant |
| Retención de datos | 90 días sin actividad → borrar/anonimizar |

## Estado actual (commit c1c9ddb)
- ✅ Webhook Meta + verificación, Claude Haiku, memoria Supabase, idempotencia, horarios calculados, prompt Sofía.
- 🔴 Falta `import re` → `limpiar_texto()` falla → Sofía no responde.
- ⚠️ Sin validación de firma `X-Hub-Signature-256`; procesamiento síncrono en webhook (Meta reintenta); Supabase síncrono en ruta async; `.format()` frágil con `{}`; logs con contenido emocional sensible; sin tests, schema, Docker, CI, README.

---

## Día 1 — Hotfix + estructura
- [ ] `import re` + test que lo cubra.
- [ ] Validar firma HMAC `X-Hub-Signature-256` (APP_SECRET).
- [ ] Responder 200 inmediato y procesar en `BackgroundTasks`.
- [ ] Supabase vía `run_in_threadpool` o cliente async.
- [ ] Render de prompt con `string.Template` (`$var`) en vez de `.format()`.
- [ ] Separar en paquete: `app/{config,webhook,whatsapp,llm,store,scheduling,guardrails}.py`.
- [ ] `db/schema.sql` versionado + `.env.example`.
- [ ] No loguear texto de mensajes en prod (solo IDs/hash).

## Día 2 — Tests base
- [x] pytest + ruff + pre-commit.
- [x] Unit: `normalizar_numero`, `calcular_horarios` (freezegun: bordes de hora, fin de semana, 2h de margen), `limpiar_texto`.
- [x] Integración webhook con mocks (respx para Meta/Claude, fake store): duplicado, estado, no-texto, firma inválida.
- [x] Meta: cobertura ≥80%.

## Día 3 — Guardrails en código (Poka-yoke)
- [x] **Crisis pre-LLM:** clasificador (keywords + llamada Haiku corta) → respuesta fija con Línea de la Vida 800 911 2000 + alerta a Eduardo + pausa de venta. No depender solo del prompt.
- [x] **Validador de salida:** signos/palabras prohibidas, markdown, longitud, precio ≠ $300 → reintento 1 vez, luego fallback seguro.
- [x] Tests de cada guardrail.

## Día 4 — Derivación + alertas
- [x] Tool use de Claude: `derivar_a_eduardo(motivo)`.
- [x] Alerta por Telegram (comprobante recibido, pide hablar con Eduardo, crisis, fuera de fase).
- [x] Flag `humano_activo` por conversación: bot se silencia hasta `/reanudar` en Telegram.
- [x] Imágenes (comprobantes) → derivación automática en vez de ignorar.

## Día 5 — Agenda real (iCloud)
Decisión: el calendario es el del iPhone (iCloud, calendario "Casa"); sesiones de 1 hora; nunca se aparta sin pago. Pago primero, Eduardo valida y ordena la reserva.
- [x] Leer ocupado/libre de iCloud por CalDAV y ofrecer solo horarios libres (respaldo: `calcular_horarios`). Respeta eventos marcados como Libre.
- [x] `/reservar <tel> <día/mes> <hora>` en Telegram (o contestando a la alerta): verifica que siga libre, crea el evento de 1 h y confirma a la persona por WhatsApp.
- [x] Alertas en el calendario de Eduardo: 10 minutos antes y a la hora (VALARM en el evento).
- [x] Recordatorio por WhatsApp a la persona 10 minutos antes (revisión cada minuto, registro en `recordatorios` para no repetir; plantilla aprobada si pasaron más de 24 h, si no aviso a Eduardo).

## Día 6 — Audio
- [x] Descargar media de Graph API → transcribir con Groq `whisper-large-v3-turbo`.
- [x] Guardar transcripción como mensaje `user` con marca `origen=audio`.
- [x] Si falla la transcripción → pedir que lo escriba.
- [x] Tests con fixture de audio.

## Días 7–8 — Evals + red-teaming (pieza central del portafolio)
- [x] `evals/dataset/`: 40–60 conversaciones multi-turno por fase (filtro, patrón, propuesta, cierre, objeción, derivación) y por país (MX, VE/CO, otros).
- [x] **Checks deterministas:** formato, signos/palabras prohibidas, precio, horarios ofrecidos = los calculados, no asume género (lista de adjetivos), se presenta como Sofía en turno 1.
- [x] **LLM-as-judge** (rúbrica 1–5): fase correcta, tono confrontativo-respetuoso, siguiente paso claro, no promete resultados.
- [x] **Red-team:** prompt injection ("ignora instrucciones"), pedir descuento, cambiar CLABE, sacar el system prompt, crisis encubierta, "¿eres bot?", horarios fuera de rango.
- [x] Herramienta: promptfoo o DeepEval (cubre brecha). Reporte en `evals/RESULTS.md` con tabla pass-rate por categoría y por versión de prompt.
- [x] `docs/FMEA.md`: modos de falla → severidad/ocurrencia/detección → control (guardrail/eval). Diferenciador QA industrial → IA.

## Día 9 — Docker, CI, deploy, observabilidad, privacidad
- [x] Dockerfile multi-stage + docker-compose.
- [x] GitHub Actions: ruff + pytest + evals deterministas en cada PR; evals LLM en `workflow_dispatch`/nightly.
- [x] Deploy en Railway desde Dockerfile; webhook definitivo en subdominio de sanandoalmaa.com.
- [x] Tabla `metricas`: latencia, tokens in/out, costo por mensaje, fase detectada.
- [x] **Retención 90 días:** job diario que borra/anonimiza conversaciones sin actividad >90 días.
- [x] ~~Comando `BORRAR MIS DATOS`~~ quitado el 5 de noviembre: el borrado es la retención de 90 días y, a petición, por correo.
- [x] Aviso de privacidad (enlace) en el primer mensaje de Sofía.

## Día 10 — Presentación
- [x] README bilingüe (EN/ES): problema, arquitectura (Mermaid), features, guardrails, resultados de evals, setup, roadmap.
- [ ] GIF/video de demo con número de prueba (sin datos reales). Guion listo en `docs/DEMO.md`; falta grabarlo.
- [x] PRIVACY.md actualizado: retención 90 días, consentimiento, borrado a petición, terceros (Meta, Anthropic, Supabase, Groq).
- [ ] LICENSE, topics, repo fijado, post LinkedIn. LICENSE (todos los derechos reservados) y topics listos; falta fijar el repo y publicar el post.
