"""Tope de mensajes entrantes. Cada mensaje cuesta Claude (y Groq en notas de voz) y puede llegarle a Eduardo por
Telegram: sin tope, una sola persona podía agotar el crédito o enterrar las alertas de crisis.

Vive en memoria: se reinicia con cada despliegue, y Sofía corre en una sola instancia."""

import time
from collections import deque

from app import config

HORA = 3600
DIA = 86400

_por_persona: dict[str, deque] = {}
_total: deque = deque()
_avisados: dict[str, float] = {}  # quién ya recibió el aviso de tope, para avisar una sola vez por ventana


def reiniciar() -> None:
    _por_persona.clear()
    _total.clear()
    _avisados.clear()


def _recortar(marcas: deque, ahora: float, ventana: int) -> None:
    while marcas and ahora - marcas[0] >= ventana:
        marcas.popleft()


def revisar(remitente: str, ahora: float | None = None) -> str | None:
    """Cuenta el mensaje. Devuelve None si se atiende, o "persona" / "total" si pasó un tope."""
    ahora = time.monotonic() if ahora is None else ahora
    marcas = _por_persona.setdefault(remitente, deque())
    _recortar(marcas, ahora, HORA)
    _recortar(_total, ahora, DIA)
    if len(marcas) >= config.LIMITE_MENSAJES_HORA:
        return "persona"
    if len(_total) >= config.LIMITE_MENSAJES_DIA:
        return "total"
    marcas.append(ahora)
    _total.append(ahora)
    return None


def primer_aviso(clave: str, ventana: int, ahora: float | None = None) -> bool:
    """True la primera vez por ventana, para no repetir el aviso con cada mensaje de más."""
    ahora = time.monotonic() if ahora is None else ahora
    if clave in _avisados and ahora - _avisados[clave] < ventana:
        return False
    _avisados[clave] = ahora
    return True
