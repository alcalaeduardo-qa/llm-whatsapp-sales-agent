import logging
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

from app import calendario, config
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


NOTA_DEVUELTA = (
    "CONVERSACIÓN DEVUELTA POR EDUARDO\n"
    "Eduardo ya atendió personalmente a esta persona (sus mensajes aparecen con el prefijo [Eduardo]) "
    "y te devolvió la conversación. Continúa tú desde donde quedó. No vuelvas a usar derivar_a_eduardo "
    "por lo que se dijo antes; úsala solo si en su último mensaje la persona lo pide de nuevo, envía un "
    "comprobante o pregunta algo fuera de las fases. Nunca escribas el prefijo [Eduardo]."
)

# {cuando}: "martes 3 de noviembre a las 3 PM" (o varias unidas con " y el ")
NOTA_SESION_RESERVADA = (
    "SESIÓN YA RESERVADA\n"
    "Esta persona ya pagó y tiene sesión reservada con Eduardo el {cuando} hora México, según su calendario. "
    "Es un hecho confirmado aunque el historial no muestre el pago. No ofrezcas otros horarios, no pidas pago "
    "y no vuelvas a vender. Si pregunta por su sesión, confírmale esa fecha y hora. Si quiere cambiarla o "
    "cancelarla, usa derivar_a_eduardo con motivo otro."
)


@dataclass
class Respuesta:
    texto: str
    derivacion: dict | None = None  # {"motivo": ..., "resumen": ...} si Claude llamó a la herramienta


@lru_cache
def _cliente():
    from anthropic import AsyncAnthropic

    return AsyncAnthropic()


async def generar_respuesta(
    historial: list[dict], correccion: str | None = None, contexto: str | None = None
) -> Respuesta:
    ahora = datetime.now(config.ZONA_MX)
    system = cargar_prompt(ahora, await calendario.opciones_horario(ahora))
    if contexto:
        system += f"\n\n{contexto}"
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
        output_config={"effort": config.SOFIA_EFFORT},
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
        max_tokens=256,
        # Sin razonamiento: con él activo, el SI/NO no alcanzaba a salir y siempre contaba como NO
        thinking={"type": "disabled"},
        output_config={"effort": "low"},
        system=PROMPT_CLASIFICADOR,
        messages=[{"role": "user", "content": "\n".join(mensajes)}],
    )
    texto = "".join(b.text for b in respuesta.content if b.type == "text").strip().upper()
    if respuesta.stop_reason != "end_turn" or not texto:
        # Sin veredicto no se asume "NO": quien llama recurre a la lista de señales
        raise RuntimeError(f"Clasificador sin veredicto (stop_reason={respuesta.stop_reason})")
    return texto.startswith(("SI", "SÍ"))
