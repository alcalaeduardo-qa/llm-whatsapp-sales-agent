import json
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app import config, llm, store, whatsapp
from app.text import huella, normalizar_numero

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("sofia")

app = FastAPI(title="Sofía · WhatsApp sales agent")


def _desc(texto: str) -> str:
    """Contenido completo solo si LOG_CONTENIDO=true; si no, longitud + huella."""
    if config.LOG_CONTENIDO:
        return repr(texto)
    return f"<{len(texto)} chars #{huella(texto)}>"


async def procesar_mensaje(remitente: str, texto: str, wa_message_id: str) -> None:
    try:
        es_nuevo = await store.registrar_entrante(remitente, texto, wa_message_id)
        if not es_nuevo:
            logger.info("Mensaje duplicado %s, se ignora", wa_message_id)
            return

        historial = await store.obtener_historial(remitente)
        respuesta = await llm.generar_respuesta(historial)
        if not respuesta:
            logger.warning("Claude no devolvió texto (msg %s)", wa_message_id)
            return

        logger.info("Sofía responde %s", _desc(respuesta))
        await store.guardar_respuesta(remitente, respuesta)
        await whatsapp.enviar_texto(normalizar_numero(remitente), respuesta)
    except Exception:
        logger.exception("Error procesando mensaje %s", wa_message_id)


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
        raise HTTPException(status_code=400, detail="JSON inválido")

    mensaje = whatsapp.extraer_mensaje(body)
    if mensaje is None:
        return {"status": "ignorado"}

    if mensaje["type"] != "text" or not mensaje["text"]:
        logger.info("Mensaje tipo %s no soportado aún (msg %s)", mensaje["type"], mensaje["id"])
        return {"status": "ignorado"}

    logger.info("Mensaje entrante %s %s", mensaje["id"], _desc(mensaje["text"]))
    # Se responde 200 de inmediato; Meta reintenta si el webhook tarda.
    tareas.add_task(procesar_mensaje, mensaje["from"], mensaje["text"], mensaje["id"])
    return {"status": "recibido"}
