"""Hace pasar una conversación del dataset por el flujo real de Sofía, sin WhatsApp ni base de datos.

Por cada mensaje de la persona: detección de crisis → (si no hay crisis) Claude con el prompt real,
validador y reintento → respuesta. Igual que en producción, la conversación termina cuando Sofía
deriva a Eduardo o activa el protocolo de crisis.
"""

from dataclasses import dataclass, field

from app import config, guardrails, main

# Datos de pago ficticios: los resultados no dependen del .env y nunca muestran los reales
DATOS_PAGO_PRUEBA = {
    "banco": "Banco de Prueba",
    "clabe": "012180001234567891",
    "titular": "Eduardo Prueba",
    "link_stripe": "https://buy.stripe.com/test_sofia",
    "link_guia": "https://sanandoalmaa.com/guia-prueba",
}


@dataclass
class Conversacion:
    escenario: dict
    turnos: list[dict] = field(default_factory=list)  # {"role": "user"|"assistant", "content": str}
    derivacion: dict | None = None
    crisis: bool = False
    error: str | None = None


def preparar_entorno() -> None:
    """Configuración fija para que los evals midan a Sofía y no al entorno."""
    config.DATOS_PAGO.update(DATOS_PAGO_PRUEBA)
    config.ICLOUD_USUARIO = ""  # horarios calculados, sin depender del calendario real
    config.ICLOUD_PASSWORD_APP = ""


async def conversar(escenario: dict) -> Conversacion:
    conversacion = Conversacion(escenario)
    historial: list[dict] = []
    estado = {"venta_pausada": False, "humano_activo": False, "atendida_por_eduardo": False}
    try:
        for texto in escenario["turnos"]:
            historial.append({"role": "user", "content": texto})
            conversacion.turnos.append({"role": "user", "content": texto})
            recientes = [m["content"] for m in historial if m["role"] == "user"][-3:]
            if await guardrails.detectar_crisis(recientes):
                conversacion.crisis = True
                respuesta = guardrails.RESPUESTA_CRISIS
            else:
                contexto = await main.contexto_de("eval", estado, historial)
                resultado = await main.generar_validada(historial, "eval", contexto)
                respuesta = resultado.texto
                conversacion.derivacion = resultado.derivacion
            historial.append({"role": "assistant", "content": respuesta})
            conversacion.turnos.append({"role": "assistant", "content": respuesta})
            if conversacion.crisis or conversacion.derivacion:
                break
    except Exception as e:  # una falla de API no debe tumbar toda la corrida
        conversacion.error = f"{type(e).__name__}: {e}"
    return conversacion
