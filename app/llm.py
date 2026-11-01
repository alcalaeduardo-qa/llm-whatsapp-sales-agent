import logging
from functools import lru_cache

from app import config
from app.prompt import cargar_prompt
from app.text import limpiar_texto

logger = logging.getLogger("sofia.llm")


@lru_cache
def _cliente():
    from anthropic import AsyncAnthropic

    return AsyncAnthropic()


async def generar_respuesta(historial: list[dict]) -> str:
    respuesta = await _cliente().messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=config.MAX_TOKENS,
        system=cargar_prompt(),
        messages=historial,
    )
    if respuesta.stop_reason == "max_tokens":
        logger.warning("Respuesta cortada por límite de tokens")
    logger.info(
        "Claude uso tokens in=%s out=%s",
        respuesta.usage.input_tokens,
        respuesta.usage.output_tokens,
    )
    textos = [b.text for b in respuesta.content if b.type == "text"]
    return limpiar_texto("\n".join(textos))
