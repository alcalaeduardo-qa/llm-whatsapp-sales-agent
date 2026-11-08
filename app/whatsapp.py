import asyncio
import hashlib
import hmac
import logging
from datetime import datetime, timedelta

import httpx

from app import config

logger = logging.getLogger("sofia.whatsapp")

# Archivos que pueden ser un comprobante de pago
TIPOS_MEDIA = {"image", "document"}
AUDIO = "audio"  # notas de voz: se transcriben

# Ventana de servicio de WhatsApp (24 h), con 10 minutos de margen
VENTANA = timedelta(hours=23, minutes=50)
FUERA_DE_VENTANA = 131047


def firma_valida(cuerpo: bytes, encabezado: str | None, secreto: str | None = None) -> bool:
    """Valida X-Hub-Signature-256 (HMAC-SHA256 del cuerpo con el App Secret de Meta)."""
    secreto = secreto or config.APP_SECRET
    if not secreto or not encabezado or not encabezado.startswith("sha256="):
        return False
    esperado = hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, encabezado.removeprefix("sha256="))


def _parsear_mensaje(m: dict) -> dict:
    media = m.get(m["type"]) if m["type"] in TIPOS_MEDIA or m["type"] == AUDIO else None
    return {
        "from": m["from"],
        "id": m["id"],
        "type": m["type"],
        "text": m.get("text", {}).get("body"),
        "media_id": media.get("id") if media else None,
        "caption": media.get("caption") if media else None,
    }


def extraer_mensajes(body: dict) -> list[dict]:
    """Todos los mensajes del cuerpo, en orden: {'from','id','type','text','media_id','caption'}.

    Meta puede agrupar varios mensajes (de una o varias personas) en una misma entrega firmada. Un mensaje
    mal formado se omite sin tumbar a los demás."""
    mensajes = []
    try:
        entradas = list(body["entry"])
    except (KeyError, TypeError):
        return mensajes
    for entrada in entradas:
        try:
            cambios = list(entrada.get("changes") or [])
        except AttributeError:
            continue
        for cambio in cambios:
            try:
                crudos = cambio["value"].get("messages") or []
            except (KeyError, TypeError, AttributeError):
                continue
            for crudo in crudos:
                try:
                    mensajes.append(_parsear_mensaje(crudo))
                except (KeyError, TypeError, AttributeError):
                    continue
    return mensajes


def extraer_mensaje(body: dict) -> dict | None:
    """El primer mensaje del cuerpo, o None si es un evento sin mensaje."""
    mensajes = extraer_mensajes(body)
    return mensajes[0] if mensajes else None


def extraer_fallos(body: dict) -> list[dict]:
    """Estados "failed" que Meta manda después de aceptar un envío: {'destino','codigo','detalle'}."""
    try:
        estados = body["entry"][0]["changes"][0]["value"].get("statuses") or []
    except (KeyError, IndexError, TypeError, AttributeError):
        return []
    fallos = []
    for estado in estados:
        if estado.get("status") != "failed":
            continue
        error = (estado.get("errors") or [{}])[0]
        fallos.append(
            {
                "destino": estado.get("recipient_id", "?"),
                "codigo": error.get("code"),
                "detalle": (error.get("error_data") or {}).get("details") or error.get("title", ""),
            }
        )
    return fallos


def dentro_de_ventana(ultimo_mensaje: datetime | None, ahora: datetime) -> bool:
    """WhatsApp solo deja mandar texto libre hasta 24 h después del último mensaje de la persona."""
    return ultimo_mensaje is not None and ahora - ultimo_mensaje < VENTANA


MAX_MEDIA_BYTES = 16 * 1024 * 1024  # un comprobante o una nota de voz pesan mucho menos
_DESCARGAS = asyncio.Semaphore(3)  # pocas a la vez: cada archivo vive completo en memoria hasta reenviarse


class ArchivoMuyGrande(Exception):
    pass


async def descargar_media(media_id: str) -> tuple[bytes, str]:
    """Graph API en dos pasos: el id da una URL temporal y esa URL (con el token) da el archivo.
    Se corta al pasar MAX_MEDIA_BYTES para que un archivo enorme no agote la memoria del servidor."""
    headers = {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}
    async with _DESCARGAS, httpx.AsyncClient(timeout=30) as client:
        meta = await client.get(f"https://graph.facebook.com/{config.GRAPH_API_VERSION}/{media_id}", headers=headers)
        meta.raise_for_status()
        info = meta.json()
        if int(info.get("file_size") or 0) > MAX_MEDIA_BYTES:
            raise ArchivoMuyGrande(info.get("file_size"))
        partes, total = [], 0
        async with client.stream("GET", info["url"], headers=headers) as archivo:
            archivo.raise_for_status()
            async for parte in archivo.aiter_bytes():
                total += len(parte)
                if total > MAX_MEDIA_BYTES:
                    raise ArchivoMuyGrande(total)
                partes.append(parte)
    return b"".join(partes), info.get("mime_type", "application/octet-stream")


async def enviar_texto(destino: str, texto: str) -> None:
    await _enviar({"messaging_product": "whatsapp", "to": destino, "type": "text", "text": {"body": texto}})


async def enviar_plantilla(destino: str, nombre: str, parametros: list[str]) -> None:
    """Plantilla aprobada por Meta: lo único que se puede enviar pasadas 24 h del último mensaje de la persona."""
    await _enviar(
        {
            "messaging_product": "whatsapp",
            "to": destino,
            "type": "template",
            "template": {
                "name": nombre,
                "language": {"code": config.WHATSAPP_PLANTILLA_IDIOMA},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in parametros]}],
            },
        }
    )


def _resumen_de_error(r: httpx.Response) -> str:
    """Código y tipo del error de Meta, sin el cuerpo: el mensaje puede traer el teléfono o el texto."""
    try:
        error = r.json()["error"]
        return f"código {error.get('code')}, tipo {error.get('type')}, subcódigo {error.get('error_subcode')}"
    except (ValueError, KeyError, TypeError, AttributeError):
        return "sin detalle"


async def _enviar(data: dict) -> None:
    url = f"https://graph.facebook.com/{config.GRAPH_API_VERSION}/{config.PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, headers=headers, json=data)
    if r.is_error:
        logger.error("Meta respondió %s (%s)", r.status_code, _resumen_de_error(r))
        r.raise_for_status()
    logger.info("Mensaje enviado (status %s)", r.status_code)
