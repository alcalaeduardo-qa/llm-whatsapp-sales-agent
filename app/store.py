"""Acceso a Supabase. El cliente es síncrono, así que se ejecuta en un hilo."""

import asyncio
import logging
from datetime import UTC, datetime
from functools import lru_cache

from app import config

logger = logging.getLogger("sofia.store")


@lru_cache
def _cliente():
    from supabase import create_client

    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


def _es_duplicado(error: Exception) -> bool:
    # 23505 = unique_violation en Postgres
    return "23505" in str(error) or "duplicate key" in str(error).lower()


def _registrar_entrante(telefono: str, contenido: str, wa_message_id: str) -> bool:
    """Inserta el mensaje del usuario. Devuelve False si ya existía (reintento de Meta)."""
    try:
        _cliente().table("mensajes").insert(
            {
                "telefono": telefono,
                "rol": "user",
                "contenido": contenido,
                "wa_message_id": wa_message_id,
            }
        ).execute()
        return True
    except Exception as e:
        if _es_duplicado(e):
            return False
        raise


def _guardar_respuesta(telefono: str, contenido: str) -> None:
    _cliente().table("mensajes").insert(
        {
            "telefono": telefono,
            "rol": "assistant",
            "contenido": contenido,
        }
    ).execute()


def _obtener_historial(telefono: str, limite: int) -> list[dict]:
    resultado = (
        _cliente()
        .table("mensajes")
        .select("rol, contenido")
        .eq("telefono", telefono)
        .order("creado_en", desc=True)
        .limit(limite)
        .execute()
    )
    return preparar_historial(list(reversed(resultado.data)))


def _venta_pausada(telefono: str) -> bool:
    resultado = _cliente().table("conversaciones").select("venta_pausada").eq("telefono", telefono).limit(1).execute()
    return bool(resultado.data and resultado.data[0]["venta_pausada"])


def _pausar_venta(telefono: str, motivo: str) -> None:
    _cliente().table("conversaciones").upsert(
        {
            "telefono": telefono,
            "venta_pausada": True,
            "motivo_pausa": motivo,
            "pausada_en": datetime.now(UTC).isoformat(),
        }
    ).execute()


def preparar_historial(filas: list[dict]) -> list[dict]:
    """La API de Claude exige iniciar con 'user'; se recortan filas iniciales de 'assistant'."""
    while filas and filas[0]["rol"] != "user":
        filas.pop(0)
    return [{"role": f["rol"], "content": f["contenido"]} for f in filas]


async def registrar_entrante(telefono: str, contenido: str, wa_message_id: str) -> bool:
    return await asyncio.to_thread(_registrar_entrante, telefono, contenido, wa_message_id)


async def guardar_respuesta(telefono: str, contenido: str) -> None:
    await asyncio.to_thread(_guardar_respuesta, telefono, contenido)


async def obtener_historial(telefono: str, limite: int = config.HISTORIAL_MAX) -> list[dict]:
    return await asyncio.to_thread(_obtener_historial, telefono, limite)


async def venta_pausada(telefono: str) -> bool:
    """Si la consulta falla (p. ej. falta la tabla) se asume no pausada: la detección de crisis
    corre en cada mensaje de todos modos."""
    try:
        return await asyncio.to_thread(_venta_pausada, telefono)
    except Exception:
        logger.exception("No se pudo leer el estado de la conversación")
        return False


async def pausar_venta(telefono: str, motivo: str) -> None:
    try:
        await asyncio.to_thread(_pausar_venta, telefono, motivo)
    except Exception:
        logger.exception("No se pudo pausar la venta (motivo %s)", motivo)
