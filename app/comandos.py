"""Comandos de Eduardo desde Telegram. Solo se aceptan del chat configurado en TELEGRAM_CHAT_ID."""

import logging
import re
from datetime import date, datetime, time, timedelta

import httpx

from app import calendario, config, guardrails, store, telegram, whatsapp
from app.scheduling import dia_y_fecha
from app.text import normalizar_numero

logger = logging.getLogger("sofia.comandos")

AYUDA = (
    "Comandos de Sofía\n\n"
    "Responder a una persona: contesta (reply) a su alerta con tu mensaje, "
    "o usa /responder <teléfono> <mensaje> (teléfono sin espacios, p. ej. 5214931234567)\n"
    "/reservar <teléfono> <día/mes> <hora>  Después de validar el pago, crea la sesión en tu calendario "
    "y le avisa a la persona (p. ej. /reservar 5214931234567 2/11 14). Contestando a su alerta basta "
    "/reservar 2/11 14\n"
    "/reanudar <teléfono>  Sofía vuelve a atender (quita modo humano y pausa por crisis)\n"
    "/pausar <teléfono>  Sofía deja de responder y te reenvía los mensajes\n"
    "/ayuda  Muestra esta lista"
)

USO_RESERVAR = (
    "Uso /reservar <teléfono> <día/mes> <hora>, p. ej. /reservar 5214931234567 2/11 14 "
    "(o contesta a su alerta con /reservar 2/11 14). También acepta hoy, mañana y 2pm."
)

# El encabezado de cada alerta trae el teléfono primero; 7 dígitos es el mínimo de un número internacional.
# Con un mínimo mayor, un número corto se saltaba y se tomaba uno escrito por la persona más abajo.
_TELEFONO_EN_ALERTA = re.compile(r"\+(\d{7,15})")
_FECHA = re.compile(r"^(\d{1,2})/(\d{1,2})$")
_HORA = re.compile(r"^(\d{1,2})(?::00)?(am|pm)?$")


def numero_meta(texto: str) -> str | None:
    """Teléfono como lo manda Meta en `from` (clave del historial). Acepta +, espacios y guiones;
    un móvil MX escrito sin el 1 (52 + 10 dígitos) se convierte a 521 + 10 dígitos."""
    digitos = re.sub(r"\D", "", texto)
    if not 10 <= len(digitos) <= 15:
        return None
    if digitos.startswith("52") and len(digitos) == 12:
        return "521" + digitos[2:]
    return digitos


def interpretar_fecha_hora(dia: str, hora: str, ahora: datetime) -> datetime | None:
    """ "2/11", "hoy" o "mañana" + "14", "2", "2pm" o "14:00". Las sesiones son por la tarde: "2" es 2 PM."""
    dia = dia.lower()
    if dia == "hoy":
        fecha = ahora.date()
    elif dia in ("mañana", "manana"):
        fecha = ahora.date() + timedelta(days=1)
    else:
        encontrado = _FECHA.match(dia)
        if not encontrado:
            return None
        try:
            fecha = date(ahora.year, int(encontrado.group(2)), int(encontrado.group(1)))
            if fecha < ahora.date():  # 5/1 escrito en diciembre es del año siguiente
                fecha = fecha.replace(year=ahora.year + 1)
        except ValueError:
            return None
    encontrado = _HORA.match(hora.lower().replace(" ", ""))
    if not encontrado or encontrado.group(2) == "am":
        return None
    numero = int(encontrado.group(1))
    if numero < 12:
        numero += 12
    if numero > 23:
        return None
    return datetime.combine(fecha, time(numero), tzinfo=config.ZONA_MX)


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


async def reservar(telefono: str, inicio: datetime) -> None:
    """Solo por orden de Eduardo, después de que él validó el pago. Sofía nunca reserva por su cuenta."""
    if not calendario.configurado():
        await telegram.alertar(
            "❌ Falta configurar iCloud (ICLOUD_USUARIO e ICLOUD_PASSWORD_APP en .env). No reservé nada."
        )
        return
    if inicio <= datetime.now(config.ZONA_MX):
        await telegram.alertar("❌ Ese horario ya pasó. No reservé nada.")
        return
    if not config.HORA_INICIO <= inicio.hour < config.HORA_FIN:
        await telegram.alertar(
            f"❌ Las sesiones empiezan entre las {config.HORA_INICIO - 12} PM y las {config.HORA_FIN - 13} PM. "
            "No reservé nada."
        )
        return
    try:
        await calendario.reservar(inicio, telefono)
    except calendario.HorarioOcupado as e:
        await telegram.alertar(f"❌ Ese horario ya está ocupado en tu calendario ({e}). No reservé nada.")
        return
    except Exception:
        logger.exception("No se pudo crear el evento en iCloud")
        await telegram.alertar("❌ No pude escribir en tu calendario de iCloud. No reservé nada, revisa los logs.")
        return

    cuando = f"{dia_y_fecha(inicio.date())} a las {inicio.hour - 12} PM"
    texto = guardrails.RESPUESTA_RESERVA.format(cuando=cuando)
    if await store.en_ventana_de_whatsapp(telefono) is False:
        aviso = (
            "⚠️ No le avisé: su último mensaje fue hace más de 24 horas y WhatsApp ya no permite escribirle "
            "texto libre. Escríbele tú por otro medio o pídele que mande un mensaje a Sofía."
        )
    else:
        try:
            await whatsapp.enviar_texto(normalizar_numero(telefono), texto)
            await store.guardar_respuesta(telefono, texto)
            aviso = "Le avisé por WhatsApp (si Meta no lo entrega, te aviso)."
        except httpx.HTTPStatusError as e:
            aviso = f"⚠️ No pude avisarle por WhatsApp (Meta respondió {e.response.status_code}), escríbele tú."
    await telegram.alertar(
        f"📅 Reservado en tu calendario {config.ICLOUD_CALENDARIO}: +{telefono}, {cuando} hora México. {aviso}"
    )


async def _reservar_desde_comando(argumentos: list[str], telefono_citado: str | None) -> None:
    telefono = numero_meta(argumentos[0]) if argumentos else None
    if telefono:
        argumentos = argumentos[1:]
    else:
        telefono = telefono_citado
    inicio = None
    if telefono and len(argumentos) >= 2:
        inicio = interpretar_fecha_hora(argumentos[0], " ".join(argumentos[1:]), datetime.now(config.ZONA_MX))
    if not inicio:
        await telegram.alertar(USO_RESERVAR)
        return
    await reservar(telefono, inicio)


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

    citado = mensaje.get("reply_to_message") or {}
    en_alerta = _TELEFONO_EN_ALERTA.search(citado.get("text") or citado.get("caption") or "")
    telefono_citado = en_alerta.group(1) if en_alerta else None

    if not texto.startswith("/"):
        if not telefono_citado:
            await telegram.alertar("Para responder, contesta (reply) a la alerta de la persona.\n\n" + AYUDA)
            return
        await responder(telefono_citado, texto)
        return

    comando, *resto = texto.split(maxsplit=2)
    comando = comando.split("@")[0].lower()  # /reanudar@MiBot → /reanudar

    if comando == "/responder":
        telefono = await _con_telefono(resto[0] if resto else None, "/responder <teléfono> <mensaje>")
        if telefono and len(resto) < 2:
            await telegram.alertar("Falta el mensaje. Uso /responder <teléfono> <mensaje>")
        elif telefono:
            await responder(telefono, resto[1])
    elif comando == "/reservar":
        await _reservar_desde_comando(texto.split()[1:], telefono_citado)
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
