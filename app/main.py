import json
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app import comandos, config, guardrails, llm, store, telegram, whatsapp
from app.text import huella, normalizar_numero

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("sofia")

app = FastAPI(title="Sofía · WhatsApp sales agent")


def _desc(texto: str) -> str:
    """Contenido completo solo si LOG_CONTENIDO=true; si no, longitud + huella."""
    if config.LOG_CONTENIDO:
        return repr(texto)
    return f"<{len(texto)} chars #{huella(texto)}>"


async def generar_validada(historial: list[dict], remitente: str) -> llm.Respuesta:
    """Respuesta de Claude que pasa el validador; un reintento con la corrección y luego respaldo fijo.

    Si Claude deriva a Eduardo no se reintenta: basta con que el texto sea válido o se usa uno fijo.
    """
    correccion = None
    for intento in (1, 2):
        respuesta = await llm.generar_respuesta(historial, correccion)
        problemas = guardrails.validar_salida(respuesta.texto)
        if respuesta.derivacion:
            if problemas:
                respuesta.texto = guardrails.RESPUESTA_DERIVACION
            return respuesta
        if not problemas:
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

        respuesta = await generar_validada(historial, remitente)
        logger.info("Sofía responde %s", _desc(respuesta.texto))
        try:
            await enviar(remitente, respuesta.texto)
        finally:
            if respuesta.derivacion:
                await derivar(remitente, respuesta.derivacion["motivo"], respuesta.derivacion["resumen"], texto)
    except Exception:
        logger.exception("Error procesando mensaje %s", wa_message_id)


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

    mensaje = whatsapp.extraer_mensaje(body)
    if mensaje is None:
        return {"status": "ignorado"}

    # Se responde 200 de inmediato; Meta reintenta si el webhook tarda.
    if mensaje["type"] in whatsapp.TIPOS_MEDIA and mensaje["media_id"]:
        logger.info("Archivo entrante %s (%s)", mensaje["id"], mensaje["type"])
        tareas.add_task(procesar_media, mensaje["from"], mensaje)
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
