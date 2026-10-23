import os
import logging
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

load_dotenv()
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sofia")

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/webhook")
def verificar(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("Webhook verificado")
        return PlainTextResponse(challenge)
    logger.warning("Verificación fallida")
    raise HTTPException(status_code=403, detail="Token inválido")

@app.post("/webhook")
async def recibir(request: Request):
    body = await request.json()

    try:
        value = body["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError):
        logger.warning("Estructura inesperada: %s", body)
        return {"status": "ignorado"}

    if "messages" not in value:
        logger.info("Evento sin mensaje, probablemente un estado")
        return {"status": "ignorado"}

    mensaje = value["messages"][0]
    remitente = mensaje["from"]
    tipo = mensaje["type"]

    if tipo != "text":
        logger.info("Mensaje tipo %s de %s, no soportado aún", tipo, remitente)
        return {"status": "ignorado"}

    texto = mensaje["text"]["body"]
    logger.info("Mensaje de %s: %s", remitente, texto)
    return {"status": "recibido"}