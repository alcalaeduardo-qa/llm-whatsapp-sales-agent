# FMEA de Sofía

Análisis de modos y efectos de falla (AMEF/FMEA), la herramienta de calidad industrial aplicada a un agente de IA que vende sesiones de acompañamiento emocional por WhatsApp. Cada modo de falla tiene un **control en código** (guardrail, diseño) y, cuando aplica, una **métrica en los evals** que lo detecta antes de producción.

**Escalas (1 a 10)**
- **S · Severidad:** 10 = daño a una persona (crisis no atendida); 7–9 = daño económico o de confianza; 4–6 = mala experiencia; 1–3 = cosmético.
- **O · Ocurrencia:** frecuencia observada en evals y pruebas en vivo con el control actual. 1 = no observada; 3 = rara (< 5 %); 5 = ocasional (5–20 %); 7+ = frecuente.
- **D · Detección:** qué tan probable es que la falla llegue a la persona sin que nada la frene. 1 = un control en código la bloquea siempre; 5 = solo la detectan los evals; 9 = nadie la detectaría.
- **RPN = S × O × D.** Prioridad: RPN ≥ 100, o S ≥ 9 con D ≥ 4.

| # | Modo de falla | Efecto | S | O | D | RPN | Control actual | Detección en evals |
|---|---|---|---|---|---|---|---|---|
| 1 | No detectar una crisis dicha de forma encubierta | Persona en riesgo recibe una venta | 10 | 2 | 3 | 60 | Palabras explícitas + clasificador Haiku sin thinking; si el clasificador falla, señales ambiguas cuentan como crisis (Día 3/5) | `crisis` en `crisis-encubierta-*` |
| 2 | Tratar como crisis algo que no lo es (patrones de pareja, "me muero de ganas") | Corta una venta legítima | 4 | 1 | 3 | 12 | Clasificador con criterios explícitos de RIESGO/NORMAL; 60/60 casos sin riesgo bien descartados | `no_crisis`, `evals/clasificador.py` |
| 3 | Dar otro precio o un descuento | Pérdida económica, precedente | 8 | 2 | 1 | 16 | Validador de salida: solo $300, 136 MXN u 8 USD; reintento y respaldo | `no_descuento`, `formato` |
| 4 | Dar una CLABE o cuenta distinta (manipulación) | Fraude: el dinero va a otra cuenta | 9 | 1 | 2 | 18 | Los datos de pago se inyectan desde config, nunca del chat; el validador de salida rechaza cualquier CLABE, tarjeta o enlace que no sea el configurado | `clabe_intacta` en `clabe-*` y `tests/test_pagos.py` |
| 5 | Revelar el prompt o instrucciones internas | Copia del método de venta | 5 | 1 | 4 | 20 | Prompt fuera del repo; el modelo se niega | `no_revela_prompt` en `prompt-*`, `inyeccion-*` |
| 6 | Prometer resultados o plazos | Expectativa falsa, reclamo | 7 | 2 | 5 | 70 | Regla en el prompt | Juez `tono_y_promesas` |
| 7 | Asumir el género de la persona ("juntos", "estás lista") | Experiencia excluyente | 3 | 2 | 3 | 18 | "juntos/juntas" se reemplazan por "en conjunto" en código; el resto, regla del prompt | `genero` (38 % → 2 %) |
| 8 | Ofrecer un horario fuera de 2–7 PM o ya ocupado | Cita imposible | 6 | 2 | 2 | 24 | Horarios calculados desde el calendario Casa; `/reservar` vuelve a verificar | `horarios` en `horario-*` |
| 9 | Reservar o apartar sin pago | Agenda bloqueada, pérdida | 8 | 1 | 1 | 8 | Diseño: solo Eduardo reserva con `/reservar` tras validar el comprobante | — (por diseño) |
| 10 | Negar una reserva existente | Confusión con quien ya pagó | 7 | 2 | 3 | 42 | `NOTA_SESION_RESERVADA` desde el calendario (encontrado en vivo, Día 5) | — (prueba en vivo) |
| 11 | Respuesta vacía o cortada | Silencio o respaldo innecesario | 6 | 2 | 2 | 24 | `max_tokens` 16000 + `effort` low; respaldo que deriva (encontrado en vivo, Día 4) | `sin_error`, `no_deriva` |
| 12 | Mensaje que Meta acepta y no entrega (ventana 24 h) | La persona no recibe confirmación | 6 | 3 | 2 | 36 | Revisión de ventana antes de enviar + aviso a Eduardo de todo estado `failed` (encontrado en vivo, Día 5) | — (prueba en vivo) |
| 13 | Volver a derivar después de `/reanudar` | Eduardo recibe mensajes que Sofía debía atender | 4 | 2 | 3 | 24 | Prefijo `[Eduardo]` + `NOTA_DEVUELTA` (encontrado en vivo, Día 4) | — (test de regresión) |
| 14 | Sofía se hace pasar por Eduardo | Engaño a la persona | 7 | 1 | 1 | 7 | Validador rechaza el prefijo `[Eduardo]` | `formato` |
| 15 | Día de la semana equivocado ("mañana martes" en domingo) | Cita en el día incorrecto | 7 | 1 | 2 | 14 | Fechas en español con día y fecha explícitos (encontrado en vivo, Día 4) | `horarios` + juez de fase |
| 16 | Transcripción errónea de una nota de voz | Respuesta a algo que no se dijo | 5 | 3 | 5 | 75 | `NOTA_AUDIO`: Claude sabe que es transcripción y pregunta si algo no se entiende | — (pendiente: casos de audio) |
| 17 | Responder dos veces por reintentos de Meta | Mensajes duplicados | 4 | 1 | 1 | 4 | Índice único por `wa_message_id`; registro antes de recordatorios | — (tests) |
| 18 | Contenido sensible o tokens en los logs | Exposición de datos emocionales o credenciales | 6 | 1 | 2 | 12 | `LOG_CONTENIDO=false` (huella en vez de texto); logger `httpx` en WARNING | — (tests) |
| 19 | Webhook falsificado | Mensajes inyectados por un tercero | 8 | 1 | 1 | 8 | Firma HMAC de Meta y secret token de Telegram | — (tests) |
| 20 | Salir de la fase correcta (adelantar precio o datos de pago) | Venta torpe, menor conversión | 5 | 2 | 5 | 50 | Fases en el prompt | Juez `fase_y_siguiente_paso` (94 %), `no_datos_pago` |
| 21 | El clasificador de crisis "contesta" la pregunta de la persona ("¿hablo con una IA?" → "Sí") | Protocolo de crisis por una pregunta | 6 | 1 | 2 | 12 | Etiquetas RIESGO/NORMAL y mensajes delimitados como datos (encontrado por los evals) | `bot-*`, `evals/clasificador.py` |
| 22 | Hora equivalente mal calculada para otro país ("Houston = 12 PM") | Cita a la hora equivocada | 6 | 1 | 2 | 12 | La hora local de cada opción se calcula en código con `zoneinfo` (11 zonas, horario de verano incluido) y Claude solo la copia | `test_horas_equivalentes`, casos `cierre-otro-*` |
| 23 | Mensaje demasiado largo que terminaba en el respaldo | Derivación innecesaria a Eduardo | 4 | 1 | 1 | 4 | El largo es regla suave: se envía si es la única falla (encontrado por los evals) | `no_deriva` |
| 24 | No aceptar un horario válido que la persona propone | Venta perdida | 4 | 2 | 5 | 40 | Regla de agenda en el prompt | Juez `fase_y_siguiente_paso` |

## Prioridades

Ocurrencias de la cuarta corrida de evals (3 de noviembre de 2026, 89.1 % de casos pasan; ver `evals/RESULTS.md`).

1. ~~**#22 Hora equivalente (RPN 90)**~~ resuelto el 3 de noviembre: se calcula en código (RPN 12). Verificado con Houston, Chicago y España contra Claude real.
2. **#16 Notas de voz (RPN 75):** sin casos de eval todavía. Acción: agregar transcripciones con errores al dataset.
3. **#6 Promesas (RPN 70) y #20 Fase (RPN 50):** solo los detecta el juez; ambos en 94 %. Acción: seguirlos por versión del prompt en `evals/historial.json`.
4. **#1 Crisis (S 10):** RPN bajo con tres capas de control; 27/27 crisis reales detectadas. Cualquier falla en `crisis-*` bloquea un cambio de prompt.

## Lo que encontraron los evals

| Corrida | Casos que pasan | Qué cambió antes de correrla |
|---|---|---|
| 1 | 32.8 % | — (línea base) |
| 2 | 46.9 % | "juntos" bloqueado, clasificador afinado, límite de 1000 caracteres, derivación si la promete; juez sin penalizar lo que no aplica |
| 3 | 57.8 % | "juntos" se reemplaza en vez de bloquearse (bloquearlo mandaba conversaciones al respaldo); clasificador con etiquetas RIESGO/NORMAL |
| 4 | 89.1 % | Juez calibrado con el prompt real como especificación; el largo deja de mandar al respaldo |

Lección principal: la mitad de la mejora vino de **calibrar la medición**, no de cambiar a Sofía. Un juez que no conoce la especificación castiga al agente por seguirla.
