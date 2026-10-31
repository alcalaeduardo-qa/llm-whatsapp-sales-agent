import hashlib
import hmac
import logging

import httpx

from app import config

logger = logging.getLogger("sofia.whatsapp")


def firma_valida(cuerpo: bytes, encabezado: str | None, secreto: str | None = None) -> bool:
    """Valida X-Hub-Signature-256 (HMAC-SHA256 del cuerpo con el App Secret de Meta)."""
    secreto = secreto or config.APP_SECRET
    if not secreto or not encabezado or not encabezado.startswith("sha256="):
        return False
    esperado = hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, encabezado.removeprefix("sha256="))


def extraer_mensaje(body: dict) -> dict | None:
    """Devuelve {'from','id','type','text'} del primer mensaje, o None si es un evento sin mensaje."""
    try:
        value = body["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError, TypeError):
        return None
    mensajes = value.get("messages")
    if not mensajes:
        return None
    m = mensajes[0]
    return {
        "from": m["from"],
        "id": m["id"],
        "type": m["type"],
        "text": m.get("text", {}).get("body"),
    }


async def enviar_texto(destino: str, texto: str) -> None:
    url = f"https://graph.facebook.com/{config.GRAPH_API_VERSION}/{config.PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}
    data = {
        "messaging_product": "whatsapp",
        "to": destino,
        "type": "text",
        "text": {"body": texto},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, headers=headers, json=data)
    if r.is_error:
        logger.error("Meta respondió %s: %s", r.status_code, r.text)
        r.raise_for_status()
    logger.info("Mensaje enviado (status %s)", r.status_code)
