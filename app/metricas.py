"""Día 9: una fila por respuesta de Sofía con latencia, tokens, costo y fase.

Las filas no llevan teléfono ni contenido: son datos de operación, no de la persona, y por eso no las
toca la retención de 90 días. Una métrica que falla nunca afecta la conversación.
"""

import logging
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from app import config, store

logger = logging.getLogger("sofia.metricas")

# USD por millón de tokens (entrada, salida), para prompts de hasta 100 mil tokens. El historial está
# limitado a HISTORIAL_MAX mensajes, así que Sofía no llega a la tarifa de contexto largo.
PRECIOS_USD_POR_MILLON = {"claude-haiku-5-5": (0.10, 0.50)}

_PRECIO_SESION = re.compile(r"\b300\b")


@dataclass
class Medicion:
    inicio: float = field(default_factory=time.perf_counter)
    tokens_entrada: int = 0
    tokens_salida: int = 0
    llamadas: int = 0
    costo_usd: float | None = 0.0


# Sin medición activa (evals, clasificador suelto) sumar_uso no hace nada.
_actual: ContextVar[Medicion | None] = ContextVar("medicion", default=None)


def iniciar() -> None:
    """Empieza a medir el mensaje actual. Cada tarea asíncrona tiene su propia medición."""
    _actual.set(Medicion())


def sumar_uso(modelo: str, uso) -> None:
    """Suma una llamada a Claude. Las respuestas y el clasificador de crisis cuentan por igual."""
    medicion = _actual.get()
    if medicion is None:
        return
    medicion.llamadas += 1
    medicion.tokens_entrada += uso.input_tokens
    medicion.tokens_salida += uso.output_tokens
    precio = PRECIOS_USD_POR_MILLON.get(modelo)
    if precio is None or medicion.costo_usd is None:
        medicion.costo_usd = None  # mejor sin costo que con uno inventado
        return
    medicion.costo_usd += (uso.input_tokens * precio[0] + uso.output_tokens * precio[1]) / 1_000_000


def fase_de(texto: str, derivacion: dict | None) -> str:
    """Fase según lo que Sofía acaba de mandar, sin otra llamada a Claude.

    Solo distingue lo que se ve en el texto: filtro, patrón y objeción quedan juntos en "conversacion".
    """
    if derivacion:
        return "respaldo" if derivacion["motivo"] == "respaldo" else "derivacion"
    datos_de_pago = (config.DATOS_PAGO["clabe"], config.DATOS_PAGO["link_stripe"])
    if any(dato and dato in texto for dato in datos_de_pago):
        return "cierre"
    if _PRECIO_SESION.search(texto):
        return "propuesta"
    return "conversacion"


async def registrar(fase: str) -> None:
    """Guarda la medición actual. Se llama cuando la respuesta ya salió, para incluir el envío."""
    medicion = _actual.get()
    if medicion is None:
        return
    costo = None if medicion.costo_usd is None else round(medicion.costo_usd, 8)
    fila = {
        "fase": fase,
        "modelo": config.CLAUDE_MODEL,
        "latencia_ms": round((time.perf_counter() - medicion.inicio) * 1000),
        "tokens_entrada": medicion.tokens_entrada,
        "tokens_salida": medicion.tokens_salida,
        "llamadas_claude": medicion.llamadas,
        "costo_usd": costo,
    }
    try:
        await store.guardar_metrica(fila)
    except Exception:
        logger.exception("No se pudo guardar la métrica")
