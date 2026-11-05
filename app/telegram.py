import argparse
import asyncio
import hmac
import logging

import httpx

from app import config

logger = logging.getLogger("sofia.telegram")
# httpx registra cada URL en INFO y la de la API de Telegram lleva el token del bot.
logging.getLogger("httpx").setLevel(logging.WARNING)

LIMITE_TEXTO = 3500  # Telegram corta en 4096


def _url(metodo: str) -> str:
    return f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{metodo}"


def configurado() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


async def alertar(texto: str) -> bool:
    """Avisa a Eduardo por Telegram. Nunca lanza: una alerta fallida no debe frenar la respuesta."""
    if not configurado():
        logger.error("ALERTA sin Telegram configurado: %s", texto)
        return False
    partes = partir(texto)
    enviado = True
    for numero, parte in enumerate(partes, 1):
        if len(partes) > 1:
            parte = f"({numero}/{len(partes)}) {parte}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(_url("sendMessage"), json={"chat_id": config.TELEGRAM_CHAT_ID, "text": parte})
            r.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("No se pudo enviar la alerta a Telegram (%s): %s", type(e).__name__, parte)
            enviado = False
    return enviado


def partir(texto: str) -> list[str]:
    """Parte un texto largo en mensajes de hasta LIMITE_TEXTO caracteres, sin perder el final: ahí van el
    pie con los comandos y, en modo humano, lo último que escribió la persona."""
    partes = []
    while len(texto) > LIMITE_TEXTO:
        corte = texto.rfind("\n", 0, LIMITE_TEXTO)
        corte = corte if corte > LIMITE_TEXTO // 2 else LIMITE_TEXTO  # mejor en un salto de línea si hay uno cerca
        partes.append(texto[:corte])
        texto = texto[corte:].lstrip("\n")
    partes.append(texto)
    return partes


async def enviar_archivo(contenido: bytes, nombre: str, mime: str, leyenda: str) -> bool:
    """Reenvía un archivo (p. ej. un comprobante) a Eduardo. Nunca lanza."""
    if not configurado():
        logger.error("ARCHIVO sin Telegram configurado: %s", leyenda)
        return False
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                _url("sendDocument"),
                data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": leyenda[:1000]},
                files={"document": (nombre, contenido, mime)},
            )
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        logger.error("No se pudo reenviar el archivo a Telegram (%s): %s", type(e).__name__, leyenda)
        return False


def secreto_valido(encabezado: str | None) -> bool:
    """Telegram firma cada update con X-Telegram-Bot-Api-Secret-Token (el secret_token de setWebhook)."""
    secreto = config.TELEGRAM_WEBHOOK_SECRET
    return bool(secreto and encabezado and hmac.compare_digest(secreto, encabezado))


async def registrar_webhook(url: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            _url("setWebhook"),
            json={
                "url": url,
                "secret_token": config.TELEGRAM_WEBHOOK_SECRET,
                "allowed_updates": ["message"],
                "drop_pending_updates": True,
            },
        )
    return r.json()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Registra el webhook del bot de Telegram")
    parser.add_argument("url_publica", help="p. ej. https://abcd.ngrok-free.app (sin /telegram/webhook)")
    args = parser.parse_args()
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_WEBHOOK_SECRET:
        raise SystemExit("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_WEBHOOK_SECRET en .env")
    print(asyncio.run(registrar_webhook(args.url_publica.rstrip("/") + "/telegram/webhook")))
