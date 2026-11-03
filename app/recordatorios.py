"""Recordatorio por WhatsApp a la persona antes de su sesión.

Revisa el calendario de Eduardo cada minuto y avisa por las sesiones creadas con /reservar
(título "Sesión +teléfono"). Cada envío se registra en Supabase antes de mandarlo, así que
un reinicio o dos ciclos seguidos no lo repiten.
"""

import asyncio
import logging
from datetime import datetime, timedelta

import httpx

from app import calendario, config, guardrails, store, telegram, whatsapp
from app.text import normalizar_numero

logger = logging.getLogger("sofia.recordatorios")

INTERVALO_SEGUNDOS = 60


async def revisar(ahora: datetime | None = None) -> int:
    """Envía los recordatorios que tocan ahora. Devuelve cuántos se intentaron."""
    ahora = ahora or datetime.now(config.ZONA_MX)
    hasta = ahora + timedelta(minutes=config.RECORDATORIO_MINUTOS_ANTES)
    intentados = 0
    for uid, inicio, telefono in await calendario.sesiones_entre(ahora, hasta):
        if inicio <= ahora:
            continue
        try:
            nuevo = await store.marcar_recordatorio(uid, telefono, inicio)
        except Exception:
            # Sin registro no se envía: si no, se repetiría en cada ciclo
            logger.exception("No se pudo registrar el recordatorio; no se envía")
            continue
        if nuevo:
            await enviar(telefono, inicio)
            intentados += 1
    return intentados


async def enviar(telefono: str, inicio: datetime) -> None:
    hora = f"{inicio.strftime('%I').lstrip('0')} PM"
    texto = guardrails.RESPUESTA_RECORDATORIO.format(minutos=config.RECORDATORIO_MINUTOS_ANTES, hora=hora)
    destino = normalizar_numero(telefono)
    # Fuera de la ventana de 24 h Meta acepta el texto libre y luego lo marca fallido: ni se intenta
    if await store.en_ventana_de_whatsapp(telefono) is False:
        motivo = "su último mensaje fue hace más de 24 horas"
    else:
        try:
            await whatsapp.enviar_texto(destino, texto)
            await store.guardar_respuesta(telefono, texto)
            return
        except httpx.HTTPStatusError as e:
            motivo = f"Meta respondió {e.response.status_code}"

    if config.WHATSAPP_PLANTILLA_RECORDATORIO:
        try:
            await whatsapp.enviar_plantilla(destino, config.WHATSAPP_PLANTILLA_RECORDATORIO, [hora])
            await store.guardar_respuesta(telefono, texto)
            return
        except httpx.HTTPStatusError as e:
            motivo = f"la plantilla falló, Meta respondió {e.response.status_code}"
    await telegram.alertar(
        f"⏰ No pude mandarle el recordatorio a +{telefono} (sesión a las {hora}): {motivo}. "
        "Sin una plantilla aprobada WhatsApp no deja escribirle. Escríbele tú."
    )


async def bucle() -> None:
    while True:
        try:
            await revisar()
        except Exception:
            logger.exception("Falló la revisión de recordatorios")
        await asyncio.sleep(INTERVALO_SEGUNDOS)
