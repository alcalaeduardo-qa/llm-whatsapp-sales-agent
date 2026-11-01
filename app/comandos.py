"""Comandos de Eduardo desde Telegram. Solo se aceptan del chat configurado en TELEGRAM_CHAT_ID."""

import logging
import re

import httpx

from app import config, guardrails, store, telegram, whatsapp
from app.text import normalizar_numero

logger = logging.getLogger("sofia.comandos")

AYUDA = (
    "Comandos de Sofía\n\n"
    "Responder a una persona: contesta (reply) a su alerta con tu mensaje, "
    "o usa /responder <teléfono> <mensaje> (teléfono sin espacios, p. ej. +5214931234567)\n"
    "/reanudar <teléfono>  Sofía vuelve a atender (quita modo humano y pausa por crisis)\n"
    "/pausar <teléfono>  Sofía deja de responder y te reenvía los mensajes\n"
    "/ayuda  Muestra esta lista"
)

_TELEFONO_EN_ALERTA = re.compile(r"\+(\d{10,15})")


def numero_meta(texto: str) -> str | None:
    """Teléfono como lo manda Meta en `from` (clave del historial). Acepta +, espacios y guiones;
    un móvil MX escrito sin el 1 (52 + 10 dígitos) se convierte a 521 + 10 dígitos."""
    digitos = re.sub(r"\D", "", texto)
    if not 10 <= len(digitos) <= 15:
        return None
    if digitos.startswith("52") and len(digitos) == 12:
        return "521" + digitos[2:]
    return digitos


async def responder(telefono: str, texto: str) -> None:
    await store.activar_humano(telefono, "eduardo_responde")
    try:
        await whatsapp.enviar_texto(normalizar_numero(telefono), texto)
    except httpx.HTTPStatusError as e:
        await telegram.alertar(
            f"❌ No se pudo enviar a +{telefono} (Meta respondió {e.response.status_code}). "
            "Si pasaron más de 24 horas desde su último mensaje, WhatsApp solo permite plantillas aprobadas."
        )
        return
    # Marcado para que Claude sepa que lo escribió Eduardo y no Sofía
    await store.guardar_respuesta(telefono, guardrails.MARCA_EDUARDO + texto)
    await telegram.alertar(f"✅ Enviado a +{telefono}. Sofía sigue en silencio hasta /reanudar {telefono}")


async def _con_telefono(argumento: str | None, uso: str):
    telefono = numero_meta(argumento or "")
    if not telefono:
        await telegram.alertar(f"Falta un teléfono válido. Uso {uso}")
    return telefono


async def manejar(update: dict) -> None:
    mensaje = update.get("message") or {}
    if str(mensaje.get("chat", {}).get("id")) != str(config.TELEGRAM_CHAT_ID):
        logger.warning("Update de Telegram de un chat no autorizado, se ignora")
        return
    texto = (mensaje.get("text") or "").strip()
    if not texto:
        return

    if not texto.startswith("/"):
        citado = mensaje.get("reply_to_message") or {}
        encontrado = _TELEFONO_EN_ALERTA.search(citado.get("text") or citado.get("caption") or "")
        if not encontrado:
            await telegram.alertar("Para responder, contesta (reply) a la alerta de la persona.\n\n" + AYUDA)
            return
        await responder(encontrado.group(1), texto)
        return

    comando, *resto = texto.split(maxsplit=2)
    comando = comando.split("@")[0].lower()  # /reanudar@MiBot → /reanudar

    if comando == "/responder":
        telefono = await _con_telefono(resto[0] if resto else None, "/responder <teléfono> <mensaje>")
        if telefono and len(resto) < 2:
            await telegram.alertar("Falta el mensaje. Uso /responder <teléfono> <mensaje>")
        elif telefono:
            await responder(telefono, resto[1])
    elif comando == "/reanudar":
        telefono = await _con_telefono(resto[0] if resto else None, "/reanudar <teléfono>")
        if telefono and await store.reanudar(telefono):
            await telegram.alertar(f"▶️ Sofía vuelve a atender a +{telefono}")
        elif telefono:
            await telegram.alertar(f"❌ No se pudo reanudar a +{telefono}, revisa los logs")
    elif comando == "/pausar":
        telefono = await _con_telefono(resto[0] if resto else None, "/pausar <teléfono>")
        if telefono and await store.activar_humano(telefono, "pausa_manual"):
            await telegram.alertar(f"⏸️ Sofía deja de responder a +{telefono}. Te reenviaré sus mensajes")
        elif telefono:
            await telegram.alertar(f"❌ No se pudo pausar a +{telefono}, revisa los logs")
    else:
        await telegram.alertar(AYUDA)
