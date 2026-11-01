import logging
from functools import lru_cache

from app import config
from app.prompt import cargar_prompt
from app.text import limpiar_texto

logger = logging.getLogger("sofia.llm")

PROMPT_CLASIFICADOR = (
    "Clasificas mensajes de WhatsApp que llegan a un servicio de acompañamiento emocional. "
    "Responde solo SI o NO. "
    "SI cuando la persona expresa ideas de suicidio, de hacerse daño, de no querer vivir, "
    "o que sufre abuso o violencia en este momento, aunque lo diga de forma indirecta o encubierta. "
    "NO para tristeza, ansiedad, duelo, problemas de pareja o dolor emocional sin riesgo para su vida."
)


@lru_cache
def _cliente():
    from anthropic import AsyncAnthropic

    return AsyncAnthropic()


async def generar_respuesta(historial: list[dict], correccion: str | None = None) -> str:
    system = cargar_prompt()
    if correccion:
        system += (
            "\n\nCORRECCIÓN\nTu respuesta anterior se descartó porque incumplió estas reglas "
            f"{correccion}. Escribe de nuevo el mensaje cumpliendo todas las reglas."
        )
    respuesta = await _cliente().messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=config.MAX_TOKENS,
        system=system,
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


async def clasificar_crisis(mensajes: list[str]) -> bool:
    """Llamada corta: ¿los últimos mensajes de la persona indican riesgo? Lanza si la API falla."""
    respuesta = await _cliente().messages.create(
        model=config.CLAUDE_MODEL_CLASIFICADOR,
        max_tokens=5,
        system=PROMPT_CLASIFICADOR,
        messages=[{"role": "user", "content": "\n".join(mensajes)}],
    )
    texto = "".join(b.text for b in respuesta.content if b.type == "text").strip().upper()
    return texto.startswith(("SI", "SÍ"))
