import logging

import httpx

from app import config

logger = logging.getLogger("sofia.telegram")


async def alertar(texto: str) -> bool:
    """Avisa a Eduardo por Telegram. Nunca lanza: una alerta fallida no debe frenar la respuesta."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.error("ALERTA sin Telegram configurado: %s", texto)
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={"chat_id": config.TELEGRAM_CHAT_ID, "text": texto})
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        logger.error("No se pudo enviar la alerta a Telegram (%s): %s", type(e).__name__, texto)
        return False
