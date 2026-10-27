import os
import logging
import httpx
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from anthropic import AsyncAnthropic
from supabase import create_client

load_dotenv()
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sofia")

claude = AsyncAnthropic()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

SYSTEM_PROMPT = "Eres Sofía, asistente de Sanando el Alma. Responde breve, cálida y en español."
HISTORIAL_MAX = 20

app = FastAPI()


def normalizar_numero(numero):
    if numero.startswith("521") and len(numero) == 13:
        return "52" + numero[3:]
    return numero


def guardar_mensaje(telefono, rol, contenido):
    supabase.table("mensajes").insert({
        "telefono": telefono,
        "rol": rol,
        "contenido": contenido,
    }).execute()


def obtener_historial(telefono):
    resultado = (
        supabase.table("mensajes")
        .select("rol, contenido")
        .eq("telefono", telefono)
        .order("creado_en", desc=True)
        .limit(HISTORIAL_MAX)
        .execute()
    )
    filas = list(reversed(resultado.data))
    while filas and filas[0]["rol"] != "user":
        filas.pop(0)
    return [{"role": f["rol"], "content": f["contenido"]} for f in filas]


async def generar_respuesta(historial):
    respuesta = await claude.messages.create(
        model="claude-haiku-5-5",
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=historial,
    )
    return respuesta.content[0].text


async def enviar_whatsapp(destino, texto):
    url = f"https://graph.facebook.com/v26.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    data = {
        "messaging_product": "whatsapp",
        "to": destino,
        "type": "text",
        "text": {"body": texto},
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers=headers, json=data)
    logger.info("Meta respondió %s: %s", r.status_code, r.text)


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

    guardar_mensaje(remitente, "user", texto)
    historial = obtener_historial(remitente)

    respuesta = await generar_respuesta(historial)
    logger.info("Sofía responde: %s", respuesta)
    guardar_mensaje(remitente, "assistant", respuesta)

    destino = normalizar_numero(remitente)
    await enviar_whatsapp(destino, respuesta)

    return {"status": "recibido"}