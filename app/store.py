"""Acceso a Supabase. El cliente es síncrono, así que se ejecuta en un hilo."""

import asyncio
import logging
from datetime import UTC, datetime
from functools import lru_cache

from app import config
from app.whatsapp import dentro_de_ventana

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


def _ultimo_mensaje_de(telefono: str) -> datetime | None:
    resultado = (
        _cliente()
        .table("mensajes")
        .select("creado_en")
        .eq("telefono", telefono)
        .eq("rol", "user")
        .order("creado_en", desc=True)
        .limit(1)
        .execute()
    )
    return datetime.fromisoformat(resultado.data[0]["creado_en"]) if resultado.data else None


async def ultimo_mensaje_de(telefono: str) -> datetime | None:
    """Cuándo escribió la persona por última vez (None si nunca). Lanza si la base falla."""
    return await asyncio.to_thread(_ultimo_mensaje_de, telefono)


async def en_ventana_de_whatsapp(telefono: str) -> bool | None:
    """¿Se le puede mandar texto libre (escribió en las últimas 24 h)? None si no se pudo consultar."""
    try:
        ultimo = await ultimo_mensaje_de(telefono)
    except Exception:
        logger.exception("No se pudo consultar el último mensaje de la persona")
        return None
    return dentro_de_ventana(ultimo, datetime.now(UTC))


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


# atendida_por_eduardo: la conversación pasó alguna vez por Eduardo (tiene motivo_humano).
ESTADO_INICIAL = {
    "venta_pausada": False,
    "humano_activo": False,
    "atendida_por_eduardo": False,
    "aviso_privacidad": False,
}


def _obtener_estado(telefono: str) -> dict:
    resultado = (
        _cliente()
        .table("conversaciones")
        .select("venta_pausada, humano_activo, motivo_humano, aviso_privacidad_en")
        .eq("telefono", telefono)
        .limit(1)
        .execute()
    )
    fila = resultado.data[0] if resultado.data else {}
    return {
        "venta_pausada": bool(fila.get("venta_pausada")),
        "humano_activo": bool(fila.get("humano_activo")),
        "atendida_por_eduardo": bool(fila.get("motivo_humano")),
        "aviso_privacidad": bool(fila.get("aviso_privacidad_en")),
    }


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


def _actualizar_conversacion(telefono: str, campos: dict) -> None:
    _cliente().table("conversaciones").upsert({"telefono": telefono, **campos, "actualizado_en": _ahora()}).execute()


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


async def obtener_estado(telefono: str) -> dict:
    """Si la consulta falla se asume el estado inicial: la detección de crisis corre en cada
    mensaje de todos modos."""
    try:
        return await asyncio.to_thread(_obtener_estado, telefono)
    except Exception:
        logger.exception("No se pudo leer el estado de la conversación")
        return dict(ESTADO_INICIAL)


async def _actualizar(telefono: str, campos: dict, que: str) -> bool:
    try:
        await asyncio.to_thread(_actualizar_conversacion, telefono, campos)
        return True
    except Exception:
        logger.exception("No se pudo %s", que)
        return False


async def pausar_venta(telefono: str, motivo: str) -> bool:
    campos = {"venta_pausada": True, "motivo_pausa": motivo, "pausada_en": _ahora()}
    return await _actualizar(telefono, campos, f"pausar la venta (motivo {motivo})")


async def activar_humano(telefono: str, motivo: str) -> bool:
    """Eduardo toma la conversación: Sofía deja de responder hasta /reanudar."""
    campos = {"humano_activo": True, "motivo_humano": motivo, "humano_desde": _ahora()}
    return await _actualizar(telefono, campos, f"activar modo humano (motivo {motivo})")


async def reanudar(telefono: str) -> bool:
    """Devuelve la conversación a Sofía y quita cualquier pausa."""
    return await _actualizar(telefono, {"humano_activo": False, "venta_pausada": False}, "reanudar")


async def marcar_aviso_privacidad(telefono: str) -> bool:
    """La persona ya recibió el aviso de privacidad: no se le vuelve a mandar."""
    return await _actualizar(telefono, {"aviso_privacidad_en": _ahora()}, "marcar el aviso de privacidad")


def _guardar_metrica(fila: dict) -> None:
    _cliente().table("metricas").insert(fila).execute()


async def guardar_metrica(fila: dict) -> None:
    await asyncio.to_thread(_guardar_metrica, fila)


def _marcar_recordatorio(evento_uid: str, telefono: str, inicio: str) -> bool:
    try:
        _cliente().table("recordatorios").insert(
            {"evento_uid": evento_uid, "telefono": telefono, "inicio": inicio}
        ).execute()
        return True
    except Exception as e:
        if _es_duplicado(e):
            return False
        raise


async def marcar_recordatorio(evento_uid: str, telefono: str, inicio: datetime) -> bool:
    """Registra el recordatorio antes de enviarlo. False si ya se había enviado (otro ciclo o un reinicio)."""
    return await asyncio.to_thread(_marcar_recordatorio, evento_uid, telefono, inicio.isoformat())


TABLAS_POR_PERSONA = ("mensajes", "conversaciones", "recordatorios")


def _borrar_datos(telefono: str) -> int:
    borrados = 0
    for tabla in TABLAS_POR_PERSONA:
        resultado = _cliente().table(tabla).delete().eq("telefono", telefono).execute()
        if tabla == "mensajes":
            borrados = len(resultado.data or [])
    return borrados


async def borrar_datos(telefono: str) -> int:
    """Borra todo lo que hay de esta persona (historial, estado y recordatorios). Devuelve cuántos
    mensajes había. Lanza si la base falla: quien llama debe avisar que no se pudo."""
    return await asyncio.to_thread(_borrar_datos, telefono)
