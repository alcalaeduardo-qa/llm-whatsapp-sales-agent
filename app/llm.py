import logging
from dataclasses import dataclass
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


MOTIVOS_DERIVACION = ["pide_hablar_con_eduardo", "comprobante", "fuera_de_fase", "otro"]

HERRAMIENTA_DERIVAR = {
    "name": "derivar_a_eduardo",
    "description": (
        "Pasa la conversación a Eduardo para que la atienda personalmente. Úsala cuando la persona "
        "pida hablar con Eduardo, diga que ya pagó o mande un comprobante, o pregunte algo fuera de "
        "las fases de venta. Después de usarla ya no respondes en esta conversación."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "motivo": {"type": "string", "enum": MOTIVOS_DERIVACION},
            "resumen": {
                "type": "string",
                "description": "Una o dos líneas para Eduardo con lo que necesita saber de la persona.",
            },
        },
        "required": ["motivo", "resumen"],
    },
}


@dataclass
class Respuesta:
    texto: str
    derivacion: dict | None = None  # {"motivo": ..., "resumen": ...} si Claude llamó a la herramienta


@lru_cache
def _cliente():
    from anthropic import AsyncAnthropic

    return AsyncAnthropic()


async def generar_respuesta(historial: list[dict], correccion: str | None = None) -> Respuesta:
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
        tools=[HERRAMIENTA_DERIVAR],
    )
    if respuesta.stop_reason == "max_tokens":
        logger.warning("Respuesta cortada por límite de tokens")
    logger.info(
        "Claude uso tokens in=%s out=%s",
        respuesta.usage.input_tokens,
        respuesta.usage.output_tokens,
    )
    textos = [b.text for b in respuesta.content if b.type == "text"]
    derivacion = next(
        (dict(b.input) for b in respuesta.content if b.type == "tool_use" and b.name == HERRAMIENTA_DERIVAR["name"]),
        None,
    )
    return Respuesta(limpiar_texto("\n".join(textos)), derivacion)


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
