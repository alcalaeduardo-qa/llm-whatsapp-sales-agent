"""Calendario de Eduardo en iCloud (CalDAV).

Sofía lee ocupado/libre para ofrecer solo horarios libres. Solo escribe un evento cuando Eduardo
lo ordena desde Telegram (/reservar), después de validar el pago.
"""

import asyncio
import logging
import time as reloj
import uuid
from datetime import datetime, time, timedelta
from functools import lru_cache

import icalendar

from app import config
from app.scheduling import calcular_horarios

logger = logging.getLogger("sofia.calendario")

URL_ICLOUD = "https://caldav.icloud.com"
CACHE_SEGUNDOS = 120  # iCloud es lento; la disponibilidad no cambia cada segundo
DIAS_A_CONSULTAR = 7

Intervalo = tuple[datetime, datetime]
_cache: dict = {"vence": 0.0, "dia": None, "ocupados": []}


class HorarioOcupado(Exception):
    pass


def configurado() -> bool:
    return bool(config.ICLOUD_USUARIO and config.ICLOUD_PASSWORD_APP)


@lru_cache
def _calendario():
    import caldav

    cliente = caldav.DAVClient(
        url=URL_ICLOUD, username=config.ICLOUD_USUARIO, password=config.ICLOUD_PASSWORD_APP, timeout=20
    )
    return cliente.principal().calendar(name=config.ICLOUD_CALENDARIO)


def _a_hora_mx(valor) -> datetime:
    if isinstance(valor, datetime):
        return valor.replace(tzinfo=config.ZONA_MX) if valor.tzinfo is None else valor.astimezone(config.ZONA_MX)
    return datetime.combine(valor, time(0), tzinfo=config.ZONA_MX)  # evento de todo el día


def intervalo_ocupado(componente) -> Intervalo | None:
    """(inicio, fin) de un VEVENT, o None si en el iPhone está marcado como Libre (TRANSP:TRANSPARENT)."""
    if str(componente.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT":
        return None
    inicio_original = componente.decoded("DTSTART")
    inicio = _a_hora_mx(inicio_original)
    if "DTEND" in componente:
        fin = _a_hora_mx(componente.decoded("DTEND"))
    elif "DURATION" in componente:
        fin = inicio + componente.decoded("DURATION")
    else:
        # Sin fin: un día completo si es de todo el día, si no una hora
        fin = inicio + (timedelta(days=1) if not isinstance(inicio_original, datetime) else timedelta(hours=1))
    return inicio, fin


def _leer_ocupados(desde: datetime, hasta: datetime) -> list[Intervalo]:
    eventos = _calendario().search(event=True, start=desde, end=hasta, expand=True)
    intervalos = (intervalo_ocupado(e.icalendar_component) for e in eventos)
    return sorted(i for i in intervalos if i)


async def ocupados(desde: datetime, usar_cache: bool = True) -> list[Intervalo]:
    hasta = desde + timedelta(days=DIAS_A_CONSULTAR)
    if usar_cache and _cache["dia"] == desde.date() and reloj.monotonic() < _cache["vence"]:
        return _cache["ocupados"]
    resultado = await asyncio.to_thread(_leer_ocupados, desde, hasta)
    _cache.update(vence=reloj.monotonic() + CACHE_SEGUNDOS, dia=desde.date(), ocupados=resultado)
    return resultado


def invalidar_cache() -> None:
    _cache.update(vence=0.0, dia=None, ocupados=[])


async def opciones_horario(ahora: datetime) -> str:
    """Horarios libres para el prompt. Si iCloud falla o no está configurado, horarios calculados."""
    if not configurado():
        return calcular_horarios(ahora)
    try:
        return calcular_horarios(ahora, ocupados=await ocupados(ahora))
    except Exception:
        logger.exception("No se pudo leer el calendario de iCloud; se ofrecen horarios sin consultarlo")
        return calcular_horarios(ahora)


def _crear_evento(inicio: datetime, fin: datetime, telefono: str) -> None:
    calendario_ics = icalendar.Calendar()
    calendario_ics.add("prodid", "-//Sofia Sanando el Alma//ES")
    calendario_ics.add("version", "2.0")
    evento = icalendar.Event()
    evento.add("uid", f"{uuid.uuid4()}@sofia")
    evento.add("dtstamp", datetime.now(config.ZONA_MX))
    evento.add("dtstart", inicio)
    evento.add("dtend", fin)
    evento.add("summary", f"Sesión +{telefono}")
    evento.add("description", f"Reservada por Sofía tras la aprobación de Eduardo. WhatsApp +{telefono}")
    calendario_ics.add_component(evento)
    _calendario().add_event(calendario_ics.to_ical().decode("utf-8"))


async def reservar(inicio: datetime, telefono: str) -> datetime:
    """Crea la sesión si el horario sigue libre (consulta sin caché). Devuelve el fin de la sesión."""
    fin = inicio + timedelta(minutes=config.SESION_MINUTOS)
    for ocupado_inicio, ocupado_fin in await ocupados(inicio.replace(hour=0, minute=0), usar_cache=False):
        if inicio < ocupado_fin and ocupado_inicio < fin:
            raise HorarioOcupado(f"{ocupado_inicio:%H:%M}-{ocupado_fin:%H:%M}")
    await asyncio.to_thread(_crear_evento, inicio, fin, telefono)
    invalidar_cache()
    return fin
