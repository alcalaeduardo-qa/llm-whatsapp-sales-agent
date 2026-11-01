"""Acceso a Supabase. El cliente es síncrono, así que se ejecuta en un hilo."""

import asyncio
from functools import lru_cache

from app import config


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
