import argparse
import asyncio
import hmac
import json
import logging

import httpx

from app import config
from app.text import describir

logger = logging.getLogger("sofia.telegram")
# httpx registra cada URL en INFO y la de la API de Telegram lleva el token del bot.
logging.getLogger("httpx").setLevel(logging.WARNING)

LIMITE_TEXTO = 3500  # Telegram corta en 4096
ACTUALIZACIONES = ["message", "callback_query"]  # callback_query: los toques en los botones
COMANDOS = [
    {"command": "reservar", "description": "Crear la sesión tras validar el pago"},
    {"command": "reanudar", "description": "Sofía vuelve a atender"},
    {"command": "responder", "description": "Escribirle a una persona"},
    {"command": "pausar", "description": "Sofía deja de responder"},
    {"command": "ayuda", "description": "Ver los comandos"},
]


def _url(metodo: str) -> str:
    return f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{metodo}"


def configurado() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


async def alertar(texto: str, marcado: dict | None = None) -> bool:
    """Avisa a Eduardo por Telegram. Nunca lanza: una alerta fallida no debe frenar la respuesta."""
    if not configurado():
        logger.error("ALERTA sin Telegram configurado: %s", describir(texto))
        return False
    partes = partir(texto)
    enviado = True
    for numero, parte in enumerate(partes, 1):
        if len(partes) > 1:
            parte = f"({numero}/{len(partes)}) {parte}"
        cuerpo = {"chat_id": config.TELEGRAM_CHAT_ID, "text": parte}
        if marcado and numero == len(partes):  # los botones van en el último pedazo
            cuerpo["reply_markup"] = marcado
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(_url("sendMessage"), json=cuerpo)
            r.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("No se pudo enviar la alerta a Telegram (%s): %s", type(e).__name__, describir(parte))
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


async def enviar_archivo(contenido: bytes, nombre: str, mime: str, leyenda: str, marcado: dict | None = None) -> bool:
    """Reenvía un archivo (p. ej. un comprobante) a Eduardo. Nunca lanza."""
    if not configurado():
        logger.error("ARCHIVO sin Telegram configurado: %s", describir(leyenda))
        return False
    datos = {"chat_id": config.TELEGRAM_CHAT_ID, "caption": leyenda[:1000]}
    if marcado:
        datos["reply_markup"] = json.dumps(marcado)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                _url("sendDocument"),
                data=datos,
                files={"document": (nombre, contenido, mime)},
            )
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        logger.error("No se pudo reenviar el archivo a Telegram (%s): %s", type(e).__name__, describir(leyenda))
        return False


async def contestar_boton(identificador: str) -> None:
    """Quita el reloj de carga del botón. Nunca lanza."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(_url("answerCallbackQuery"), json={"callback_query_id": identificador})
    except httpx.HTTPError as e:
        logger.warning("No se pudo contestar el botón (%s)", type(e).__name__)


async def preparar_bot() -> None:
    """Al arrancar: publica el menú de comandos y pide los toques de botones. Nunca lanza: sin esto, Sofía atiende."""
    if not configurado():
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(_url("setMyCommands"), json={"commands": COMANDOS})
            info = (await client.post(_url("getWebhookInfo"))).json().get("result", {})
            if info.get("url") and "callback_query" not in info.get("allowed_updates", []):
                await client.post(
                    _url("setWebhook"),
                    json={
                        "url": info["url"],
                        "secret_token": config.TELEGRAM_WEBHOOK_SECRET,
                        "allowed_updates": ACTUALIZACIONES,
                    },
                )
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("No se pudo preparar el bot de Telegram (%s)", type(e).__name__)


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
                "allowed_updates": ACTUALIZACIONES,
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
