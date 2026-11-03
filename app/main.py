import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app import calendario, comandos, config, guardrails, llm, recordatorios, store, telegram, transcripcion, whatsapp
from app.scheduling import dia_y_fecha
from app.text import huella, normalizar_numero

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("sofia")


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    """Con el calendario configurado, revisa cada minuto si toca mandar recordatorios."""
    tarea = asyncio.create_task(recordatorios.bucle()) if calendario.configurado() else None
    if tarea is None:
        logger.warning("iCloud sin configurar: no se enviarán recordatorios de sesión")
    yield
    if tarea:
        tarea.cancel()


app = FastAPI(title="Sofía · WhatsApp sales agent", lifespan=ciclo_de_vida)


def _desc(texto: str) -> str:
    """Contenido completo solo si LOG_CONTENIDO=true; si no, longitud + huella."""
    if config.LOG_CONTENIDO:
        return repr(texto)
    return f"<{len(texto)} chars #{huella(texto)}>"


async def generar_validada(historial: list[dict], remitente: str, contexto: str | None = None) -> llm.Respuesta:
    """Respuesta de Claude que pasa el validador; un reintento con la corrección y luego respaldo fijo.

    Si Claude deriva a Eduardo no se reintenta: basta con que el texto sea válido o se usa uno fijo.
    """
    correccion = None
    for intento in (1, 2):
        respuesta = await llm.generar_respuesta(historial, correccion, contexto)
        problemas = guardrails.validar_salida(respuesta.texto)
        if not respuesta.derivacion and not problemas and guardrails.promete_derivar(respuesta.texto):
            # Le dijo que Eduardo le escribirá pero no usó la herramienta: sin esto la alerta nunca llega
            respuesta.derivacion = {"motivo": "otro", "resumen": "Sofía le dijo que Eduardo le escribiría."}
        if respuesta.derivacion:
            if problemas:
                respuesta.texto = guardrails.RESPUESTA_DERIVACION
            return respuesta
        if not problemas:
            return respuesta
        if intento == 2 and all(guardrails.es_problema_de_largo(p) for p in problemas):
            # El largo es una regla de estilo: los evals mostraron respuestas de propuesta un poco largas que
            # terminaban en el respaldo y derivaban sin necesidad. Mejor un mensaje largo que dejar esperando.
            logger.warning("Se envía pese a %s (solo largo)", problemas)
            return respuesta
        correccion = ", ".join(problemas)
        logger.warning("Intento %s descartado por %s", intento, problemas)

    # El respaldo promete que Eduardo escribirá, así que la conversación pasa a él.
    return llm.Respuesta(
        guardrails.RESPUESTA_RESPALDO,
        {"motivo": "respaldo", "resumen": f"Sofía no logró una respuesta válida ({correccion})."},
    )


async def enviar(remitente: str, texto: str) -> None:
    await store.guardar_respuesta(remitente, texto)
    await whatsapp.enviar_texto(normalizar_numero(remitente), texto)


def _pie(remitente: str) -> str:
    return f"Contesta a este mensaje para responderle · /reanudar {remitente} para que Sofía siga"


async def derivar(remitente: str, motivo: str, resumen: str, ultimo_texto: str) -> None:
    """Eduardo toma la conversación: Sofía se calla y le reenvía lo que llegue."""
    await store.activar_humano(remitente, motivo)
    await telegram.alertar(
        f"🙋 Derivación de +{remitente} ({motivo})\n{resumen}\n\nÚltimo mensaje\n{ultimo_texto}\n\n{_pie(remitente)}"
    )


async def reenviar_a_eduardo(remitente: str, texto: str) -> None:
    """Modo humano: no responde el modelo. Una crisis explícita sí recibe la línea de ayuda."""
    prefijo = ""
    if guardrails.crisis_explicita(texto):
        prefijo = "🚨 Posible crisis, Sofía le compartió la Línea de la Vida.\n"
        try:
            await enviar(remitente, guardrails.RESPUESTA_CRISIS)
        except Exception:
            logger.exception("No se pudo enviar la respuesta de crisis en modo humano")
    await telegram.alertar(f"{prefijo}💬 +{remitente}\n{texto}\n\n{_pie(remitente)}")


async def atender_crisis(remitente: str, nueva: bool) -> None:
    """Respuesta fija (no pasa por el modelo), pausa de venta y alerta a Eduardo."""
    if nueva:
        await store.pausar_venta(remitente, "crisis")
        respuesta = guardrails.RESPUESTA_CRISIS
        alerta = (
            f"🚨 Posible crisis de +{remitente}. Sofía detuvo la venta y compartió la Línea de la Vida. "
            "Contesta a este mensaje para escribirle."
        )
    else:
        respuesta = guardrails.RESPUESTA_PAUSA
        alerta = f"Nuevo mensaje de +{remitente}, conversación en pausa por crisis. Sofía respondió con contención."

    try:
        await enviar(remitente, respuesta)
    finally:
        await telegram.alertar(alerta)


async def contexto_de(remitente: str, estado: dict, historial: list[dict] = ()) -> str | None:
    """Hechos que el historial no muestra: que Eduardo ya la atendió, sus sesiones ya reservadas
    y que hay notas de voz transcritas."""
    notas = []
    if estado["atendida_por_eduardo"]:
        notas.append(llm.NOTA_DEVUELTA)
    if any(m["role"] == "user" and m["content"].startswith(guardrails.MARCA_AUDIO) for m in historial):
        notas.append(llm.NOTA_AUDIO)
    sesiones = await calendario.sesiones_de(remitente, datetime.now(config.ZONA_MX))
    if sesiones:
        cuando = " y el ".join(f"{dia_y_fecha(s.date())} a las {s.strftime('%I').lstrip('0')} PM" for s in sesiones)
        notas.append(llm.NOTA_SESION_RESERVADA.format(cuando=cuando))
    return "\n\n".join(notas) or None


async def procesar_mensaje(remitente: str, texto: str, wa_message_id: str) -> None:
    try:
        es_nuevo = await store.registrar_entrante(remitente, texto, wa_message_id)
        if not es_nuevo:
            logger.info("Mensaje duplicado %s, se ignora", wa_message_id)
            return

        estado = await store.obtener_estado(remitente)
        if estado["humano_activo"]:
            await reenviar_a_eduardo(remitente, texto)
            return
        if estado["venta_pausada"]:
            await atender_crisis(remitente, nueva=False)
            return

        historial = await store.obtener_historial(remitente)
        recientes = [m["content"] for m in historial if m["role"] == "user"][-3:] or [texto]
        if await guardrails.detectar_crisis(recientes):
            logger.warning("Crisis detectada (msg %s)", wa_message_id)
            await atender_crisis(remitente, nueva=True)
            return

        respuesta = await generar_validada(historial, remitente, await contexto_de(remitente, estado, historial))
        logger.info("Sofía responde %s", _desc(respuesta.texto))
        try:
            await enviar(remitente, respuesta.texto)
        finally:
            if respuesta.derivacion:
                await derivar(remitente, respuesta.derivacion["motivo"], respuesta.derivacion["resumen"], texto)
    except Exception:
        logger.exception("Error procesando mensaje %s", wa_message_id)


async def procesar_audio(remitente: str, mensaje: dict) -> None:
    """Nota de voz: se transcribe y sigue el mismo camino que un texto (crisis, modo humano, venta).
    Si no se puede transcribir, se le pide que lo escriba."""
    try:
        archivo, mime = await whatsapp.descargar_media(mensaje["media_id"])
        texto = await transcripcion.transcribir(archivo, mime)
    except Exception:
        logger.exception("No se pudo transcribir la nota de voz %s", mensaje["id"])
        await audio_sin_transcribir(remitente, mensaje["id"])
        return
    await procesar_mensaje(remitente, guardrails.MARCA_AUDIO + texto, mensaje["id"])


async def audio_sin_transcribir(remitente: str, wa_message_id: str) -> None:
    try:
        if not await store.registrar_entrante(remitente, "[nota de voz sin transcribir]", wa_message_id):
            return
        estado = await store.obtener_estado(remitente)
        if estado["humano_activo"]:
            await telegram.alertar(
                f"🎤 +{remitente} mandó una nota de voz que no pude transcribir. Pídele que lo escriba.\n\n"
                f"{_pie(remitente)}"
            )
            return
        await enviar(remitente, guardrails.RESPUESTA_AUDIO_FALLIDO)
    except Exception:
        logger.exception("Error avisando de la nota de voz sin transcribir %s", wa_message_id)


async def procesar_media(remitente: str, mensaje: dict) -> None:
    """Imagen o documento: casi siempre un comprobante. Se reenvía a Eduardo y se deriva."""
    tipo = "imagen" if mensaje["type"] == "image" else "documento"
    contenido = f"[{tipo}] {mensaje['caption']}" if mensaje["caption"] else f"[{tipo}]"
    try:
        if not await store.registrar_entrante(remitente, contenido, mensaje["id"]):
            logger.info("Mensaje duplicado %s, se ignora", mensaje["id"])
            return

        nota = f"{mensaje['caption']}\n" if mensaje["caption"] else ""
        leyenda = f"🧾 +{remitente} envió un {tipo} (posible comprobante)\n{nota}{_pie(remitente)}"
        try:
            archivo, mime = await whatsapp.descargar_media(mensaje["media_id"])
            extension = mime.split("/")[-1].split(";")[0]
            await telegram.enviar_archivo(archivo, f"{tipo}_{remitente}.{extension}", mime, leyenda)
        except Exception:
            logger.exception("No se pudo descargar el %s %s", tipo, mensaje["id"])
            await telegram.alertar(leyenda + "\n⚠️ No se pudo descargar el archivo de WhatsApp.")

        estado = await store.obtener_estado(remitente)
        if not estado["humano_activo"]:
            await store.activar_humano(remitente, "comprobante")
            await enviar(remitente, guardrails.RESPUESTA_COMPROBANTE)
    except Exception:
        logger.exception("Error procesando %s %s", tipo, mensaje["id"])


async def avisar_fallos(fallos: list[dict]) -> None:
    """Meta acepta un envío y minutos después puede marcarlo fallido; Eduardo debe enterarse."""
    for fallo in fallos:
        logger.warning("Meta no entregó un mensaje a %s (código %s)", fallo["destino"], fallo["codigo"])
        if fallo["codigo"] == whatsapp.FUERA_DE_VENTANA:
            motivo = "su último mensaje fue hace más de 24 horas y WhatsApp solo permite plantillas aprobadas"
        else:
            motivo = f"código {fallo['codigo']}: {fallo['detalle']}"
        await telegram.alertar(f"⚠️ WhatsApp no entregó un mensaje a +{fallo['destino']}: {motivo}. Escríbele tú.")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/webhook")
def verificar(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    if mode == "subscribe" and token and token == config.VERIFY_TOKEN:
        logger.info("Webhook verificado")
        return PlainTextResponse(challenge)
    logger.warning("Verificación fallida")
    raise HTTPException(status_code=403, detail="Token inválido")


@app.post("/webhook")
async def recibir(request: Request, tareas: BackgroundTasks):
    cuerpo = await request.body()
    if not whatsapp.firma_valida(cuerpo, request.headers.get("X-Hub-Signature-256")):
        logger.warning("Firma inválida, se rechaza la petición")
        raise HTTPException(status_code=401, detail="Firma inválida")

    try:
        body = json.loads(cuerpo)
    except ValueError:
        raise HTTPException(status_code=400, detail="JSON inválido") from None

    fallos = whatsapp.extraer_fallos(body)
    if fallos:
        tareas.add_task(avisar_fallos, fallos)
        return {"status": "fallo_reportado"}

    mensaje = whatsapp.extraer_mensaje(body)
    if mensaje is None:
        return {"status": "ignorado"}

    # Se responde 200 de inmediato; Meta reintenta si el webhook tarda.
    if mensaje["type"] in whatsapp.TIPOS_MEDIA and mensaje["media_id"]:
        logger.info("Archivo entrante %s (%s)", mensaje["id"], mensaje["type"])
        tareas.add_task(procesar_media, mensaje["from"], mensaje)
        return {"status": "recibido"}

    if mensaje["type"] == whatsapp.AUDIO and mensaje["media_id"]:
        logger.info("Nota de voz entrante %s", mensaje["id"])
        tareas.add_task(procesar_audio, mensaje["from"], mensaje)
        return {"status": "recibido"}

    if mensaje["type"] != "text" or not mensaje["text"]:
        logger.info("Mensaje tipo %s no soportado aún (msg %s)", mensaje["type"], mensaje["id"])
        return {"status": "ignorado"}

    logger.info("Mensaje entrante %s %s", mensaje["id"], _desc(mensaje["text"]))
    tareas.add_task(procesar_mensaje, mensaje["from"], mensaje["text"], mensaje["id"])
    return {"status": "recibido"}


@app.post("/telegram/webhook")
async def recibir_telegram(request: Request, tareas: BackgroundTasks):
    if not telegram.secreto_valido(request.headers.get("X-Telegram-Bot-Api-Secret-Token")):
        logger.warning("Update de Telegram sin secreto válido, se rechaza")
        raise HTTPException(status_code=401, detail="Secreto inválido")
    try:
        update = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="JSON inválido") from None
    tareas.add_task(comandos.manejar, update)
    return {"ok": True}
