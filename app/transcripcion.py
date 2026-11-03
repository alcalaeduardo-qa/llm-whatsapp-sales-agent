"""Transcripción de notas de voz con Groq (Whisper, API compatible con la de OpenAI)."""

import logging

import httpx

from app import config

logger = logging.getLogger("sofia.transcripcion")

URL_GROQ = "https://api.groq.com/openai/v1/audio/transcriptions"
# Groq rechaza archivos por encima de su límite; las notas de voz de WhatsApp pesan mucho menos
TAMANO_MAXIMO = 25 * 1024 * 1024


class TranscripcionFallida(Exception):
    pass


def _extension(mime: str) -> str:
    """ "audio/ogg; codecs=opus" → "ogg" (Groq usa la extensión para reconocer el formato)."""
    subtipo = mime.split(";")[0].split("/")[-1].strip()
    return {"mpeg": "mp3", "x-m4a": "m4a", "mp4": "m4a"}.get(subtipo, subtipo or "ogg")


async def transcribir(audio: bytes, mime: str) -> str:
    """Texto de la nota de voz. Lanza TranscripcionFallida si no se pudo obtener."""
    if not config.GROQ_API_KEY:
        raise TranscripcionFallida("Falta GROQ_API_KEY")
    if not audio or len(audio) > TAMANO_MAXIMO:
        raise TranscripcionFallida(f"Audio vacío o demasiado grande ({len(audio)} bytes)")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                URL_GROQ,
                headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                files={"file": (f"nota.{_extension(mime)}", audio, mime.split(";")[0])},
                data={
                    "model": config.GROQ_MODELO_TRANSCRIPCION,
                    "language": "es",
                    "response_format": "json",
                    "temperature": "0",
                },
            )
        if r.is_error:
            logger.error("Groq respondió %s: %s", r.status_code, r.text[:300])
        r.raise_for_status()
        texto = r.json().get("text", "").strip()
    except (httpx.HTTPError, ValueError) as e:
        raise TranscripcionFallida(f"Groq falló ({type(e).__name__})") from e
    if not texto:
        raise TranscripcionFallida("Transcripción vacía")
    return texto
