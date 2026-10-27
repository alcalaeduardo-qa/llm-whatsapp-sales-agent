import os
import logging
import httpx
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from anthropic import AsyncAnthropic
from supabase import create_client
from datetime import datetime, timedelta, time

load_dotenv()
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sofia")

claude = AsyncAnthropic()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

HISTORIAL_MAX = 20

app = FastAPI()


def normalizar_numero(numero):
    if numero.startswith("521") and len(numero) == 13:
        return "52" + numero[3:]
    return numero

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def calcular_horarios(ahora):
    zona = ahora.tzinfo
    limite = ahora + timedelta(hours=2)
    opciones = []
    for dias_extra in range(4):
        fecha = (ahora + timedelta(days=dias_extra)).date()
        for hora in range(14, 20):
            inicio = datetime.combine(fecha, time(hora), tzinfo=zona)
            if inicio >= limite:
                if dias_extra == 0:
                    dia = "hoy"
                elif dias_extra == 1:
                    dia = "mañana"
                else:
                    dia = "el " + DIAS[fecha.weekday()]
                hora_texto = inicio.strftime("%I").lstrip("0")
                opciones.append(f"{dia} a las {hora_texto} PM")
                break
        if len(opciones) == 2:
            break
    return " o ".join(opciones)

def limpiar_texto(texto):
    texto = re.sub(r'["“”«»]', "", texto)
    texto = texto.replace("…", ".").replace("...", ".")
    texto = re.sub(r"\s[—–-]\s", ", ", texto)
    texto = texto.replace(": ", ", ").replace("; ", ", ")
    return texto.strip()

def cargar_prompt():
    with open("prompts/sofia.md", encoding="utf-8") as f:
        plantilla = f.read()
    ahora = datetime.now(ZoneInfo("America/Mexico_City"))
    return plantilla.format(
        fecha_hora=ahora.strftime("%A %d/%m/%Y %I:%M %p"),
        banco=os.getenv("BANCO"),
        clabe=os.getenv("CLABE"),
        titular=os.getenv("TITULAR"),
        link_stripe=os.getenv("LINK_STRIPE"),
        link_guia=os.getenv("LINK_GUIA"),
        opciones_horario=calcular_horarios(ahora),
    )


def ya_procesado(wa_message_id):
    resultado = (
        supabase.table("mensajes")
        .select("id")
        .eq("wa_message_id", wa_message_id)
        .limit(1)
        .execute()
    )
    return len(resultado.data) > 0


def guardar_mensaje(telefono, rol, contenido, wa_message_id=None):
    supabase.table("mensajes").insert({
        "telefono": telefono,
        "rol": rol,
        "contenido": contenido,
        "wa_message_id": wa_message_id,
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
        max_tokens=2500,
        system=cargar_prompt(),
        messages=historial,
    )
    if respuesta.stop_reason == "max_tokens":
        logger.warning("Respuesta cortada por límite de tokens")
    textos = [bloque.text for bloque in respuesta.content if bloque.type == "text"]
    return limpiar_texto("\n".join(textos))


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
    wa_message_id = mensaje["id"]

    if ya_procesado(wa_message_id):
        logger.info("Mensaje duplicado %s, se ignora", wa_message_id)
        return {"status": "duplicado"}

    if tipo != "text":
        logger.info("Mensaje tipo %s de %s, no soportado aún", tipo, remitente)
        return {"status": "ignorado"}

    texto = mensaje["text"]["body"]
    logger.info("Mensaje de %s: %s", remitente, texto)

    try:
        guardar_mensaje(remitente, "user", texto, wa_message_id)
        historial = obtener_historial(remitente)

        respuesta = await generar_respuesta(historial)
        if not respuesta:
            logger.warning("Claude no devolvió texto para %s", remitente)
            return {"status": "sin_respuesta"}

        logger.info("Sofía responde: %s", respuesta)
        guardar_mensaje(remitente, "assistant", respuesta)

        destino = normalizar_numero(remitente)
        await enviar_whatsapp(destino, respuesta)
    except Exception:
        logger.exception("Error procesando mensaje de %s", remitente)

    return {"status": "recibido"}