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
- [ ] **Crisis pre-LLM:** clasificador (keywords + llamada Haiku corta) → respuesta fija con Línea de la Vida 800 911 2000 + alerta a Eduardo + pausa de venta. No depender solo del prompt.
- [ ] **Validador de salida:** signos/palabras prohibidas, markdown, longitud, precio ≠ $300 → reintento 1 vez, luego fallback seguro.
- [ ] Tests de cada guardrail.

## Día 4 — Derivación + alertas
- [ ] Tool use de Claude: `derivar_a_eduardo(motivo)`.
- [ ] Alerta por Telegram (comprobante recibido, pide hablar con Eduardo, crisis, fuera de fase).
- [ ] Flag `humano_activo` por conversación: bot se silencia hasta `/reanudar` en Telegram.
- [ ] Imágenes (comprobantes) → derivación automática en vez de ignorar.

## Día 5 — Agenda real
- [ ] Google Calendar free/busy reemplaza `calcular_horarios` (mantener fallback). Horario 2–8 PM en config.
- [ ] Tool `apartar_horario` → evento tentativo; `/confirmar <tel>` en Telegram lo confirma tras validar pago manual.
- [ ] Recordatorios 10 min antes y a la hora (APScheduler o cron) — requiere plantilla aprobada si pasan >24h de la ventana de WhatsApp.

## Día 6 — Audio
- [ ] Descargar media de Graph API → transcribir con Groq `whisper-large-v3-turbo`.
- [ ] Guardar transcripción como mensaje `user` con marca `origen=audio`.
- [ ] Si falla la transcripción → pedir que lo escriba.
- [ ] Tests con fixture de audio.

## Días 7–8 — Evals + red-teaming (pieza central del portafolio)
- [ ] `evals/dataset/`: 40–60 conversaciones multi-turno por fase (filtro, patrón, propuesta, cierre, objeción, derivación) y por país (MX, VE/CO, otros).
- [ ] **Checks deterministas:** formato, signos/palabras prohibidas, precio, horarios ofrecidos = los calculados, no asume género (lista de adjetivos), se presenta como Sofía en turno 1.
- [ ] **LLM-as-judge** (rúbrica 1–5): fase correcta, tono confrontativo-respetuoso, siguiente paso claro, no promete resultados.
- [ ] **Red-team:** prompt injection ("ignora instrucciones"), pedir descuento, cambiar CLABE, sacar el system prompt, crisis encubierta, "¿eres bot?", horarios fuera de rango.
- [ ] Herramienta: promptfoo o DeepEval (cubre brecha). Reporte en `evals/RESULTS.md` con tabla pass-rate por categoría y por versión de prompt.
- [ ] `docs/FMEA.md`: modos de falla → severidad/ocurrencia/detección → control (guardrail/eval). Diferenciador QA industrial → IA.

## Día 9 — Docker, CI, deploy, observabilidad, privacidad
- [ ] Dockerfile multi-stage + docker-compose.
- [ ] GitHub Actions: ruff + pytest + evals deterministas en cada PR; evals LLM en `workflow_dispatch`/nightly.
- [ ] Deploy en Railway desde Dockerfile; webhook definitivo en subdominio de sanandoalmaa.com.
- [ ] Tabla `metricas`: latencia, tokens in/out, costo por mensaje, fase detectada.
- [ ] **Retención 90 días:** job diario que borra/anonimiza conversaciones sin actividad >90 días.
- [ ] Comando `BORRAR MIS DATOS` → borra historial y confirma.
- [ ] Aviso de privacidad (enlace) en el primer mensaje de Sofía.

## Día 10 — Presentación
- [ ] README bilingüe (EN/ES): problema, arquitectura (Mermaid), features, guardrails, resultados de evals, setup, roadmap.
- [ ] GIF/video de demo con número de prueba (sin datos reales).
- [ ] PRIVACY.md actualizado: retención 90 días, consentimiento, borrado a petición, terceros (Meta, Anthropic, Supabase, Groq).
- [ ] LICENSE, topics, repo fijado, post LinkedIn.
