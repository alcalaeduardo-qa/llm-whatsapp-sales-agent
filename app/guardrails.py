"""Guardrails deterministas (Poka-yoke): no dependen de que el modelo obedezca el prompt."""

import logging
import re
import unicodedata

from app import config, llm

logger = logging.getLogger("sofia.guardrails")

LINEA_DE_LA_VIDA = "800 911 2000"

RESPUESTA_CRISIS = (
    "Lo que me cuentas es muy serio y me importa que estés a salvo. "
    "Ahora mismo lo más importante eres tú, no una sesión.\n\n"
    f"Si estás en México llama hoy a la Línea de la Vida al {LINEA_DE_LA_VIDA}, "
    "es gratuita, confidencial y atiende las 24 horas. "
    "Si estás en otro país, llama a tu número local de emergencias.\n\n"
    "Le aviso a Eduardo para que te escriba personalmente. No estás en esto sin compañía."
)

RESPUESTA_PAUSA = (
    "Te leo. Eduardo ya está enterado y te escribirá personalmente.\n\n"
    f"Si sientes que estás en peligro llama ahora a la Línea de la Vida al {LINEA_DE_LA_VIDA}, "
    "es gratuita y atiende las 24 horas, o a tu número local de emergencias."
)

# Prefijo con el que se guardan en el historial los mensajes que Eduardo escribe desde Telegram.
MARCA_EDUARDO = "[Eduardo] "
# Prefijo de las notas de voz transcritas (la transcripción automática puede tener errores).
MARCA_AUDIO = "[nota de voz] "

RESPUESTA_AUDIO_FALLIDO = "Recibí tu nota de voz, pero no logré escucharla bien. ¿Me lo puedes escribir por aquí?"

RESPUESTA_DERIVACION = "Gracias por contarme. Eduardo te escribirá personalmente a la brevedad."

RESPUESTA_COMPROBANTE = "Gracias, recibí tu archivo. Eduardo lo revisa y te confirma personalmente a la brevedad."

# {cuando}: "lunes 2 de noviembre a las 2 PM"
RESPUESTA_RESERVA = (
    "Tu pago quedó confirmado y tu sesión con Eduardo está reservada para el {cuando} hora México.\n\n"
    "Es una hora con Eduardo por escrito y audios en vivo, sin cámara. Si necesitas cambiarla, escríbeme por aquí."
)

# {minutos}: 10, {hora}: "2 PM"
RESPUESTA_RECORDATORIO = (
    "Hola, te recuerdo que tu sesión con Eduardo empieza en {minutos} minutos, a las {hora} hora México. "
    "Busca un lugar tranquilo donde puedas escribir y mandar audios con calma."
)

# {url}: config.URL_PRIVACIDAD. Va al pie de la primera respuesta de Sofía y no entra al historial,
# para que Claude no lo copie en sus mensajes.
AVISO_PRIVACIDAD = (
    "Aviso de privacidad: {url}\n"
    "Tu conversación se borra sola después de 90 días sin actividad. "
    "Si quieres que se borre antes, escribe a sanandoalmaa@gmail.com."
)

RESPUESTA_RESPALDO = "Gracias por escribirme. Eduardo te escribirá personalmente a la brevedad para darte seguimiento."


def normalizar(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar sin depender de cómo escriba la persona."""
    sin_acentos = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sin_acentos if not unicodedata.combining(c)).lower()


# --- Crisis ----------------------------------------------------------------
# Señales explícitas: bastan por sí solas, sin consultar al modelo.
_CRISIS_FUERTE = re.compile(
    r"suicid|quitarme la vida|matarme|me quiero morir|quiero morir|no quiero (seguir )?vivi(r|endo)"
    r"|acabar con (mi vida|todo)|terminar con mi vida|hacerme dano|lastimarme|cortarme|me corto"
    r"|autolesi|mejor muert[oa]|estarian mejor sin mi|sin mi estarian mejor|desaparecer para siempre"
    r"|tomarme (todas )?las pastillas|me (pega|golpea)|me viol[oa]|abusa(ron)? de mi|abuso sexual"
)
# Señales ambiguas: si el clasificador no está disponible, se tratan como crisis (falla segura).
_CRISIS_DEBIL = re.compile(
    r"\bmorir|\bmuerte\b|ya no puedo mas|no aguanto mas|desaparecer|no tiene sentido|sin salida"
    r"|rendirme|pastillas|me hace dano|abuso|golpes|no vale la pena seguir"
)


# Frases con las que Sofía le promete a la persona que Eduardo la contactará
_PROMESA_DERIVAR = re.compile(
    r"eduardo te (escribira|contactara|buscara|respondera)|le paso tu caso|le (aviso|comento) a eduardo|"
    r"eduardo se pondra en contacto|eduardo te va a escribir",
)


def promete_derivar(texto: str) -> bool:
    return bool(_PROMESA_DERIVAR.search(normalizar(texto)))


def crisis_explicita(texto: str) -> bool:
    return bool(_CRISIS_FUERTE.search(normalizar(texto)))


def senal_debil(texto: str) -> bool:
    return bool(_CRISIS_DEBIL.search(normalizar(texto)))


async def detectar_crisis(mensajes: list[str], actual: str | None = None) -> bool:
    """`mensajes`: últimos textos de la persona, para darle contexto al clasificador.
    `actual`: el mensaje que se está procesando; si no se indica, el último de `mensajes`.

    Quien procesa un mensaje debe pasar `actual`: la lista sale de la historia guardada y, si llegan dos mensajes
    seguidos, su último elemento puede ser el otro.

    Palabras explícitas → crisis sin consultar al modelo. Si no, decide un clasificador corto
    (detecta crisis encubierta). Si el clasificador falla, cualquier señal ambigua cuenta como crisis.
    """
    actual = mensajes[-1] if actual is None else actual
    if crisis_explicita(actual):
        return True
    if not config.CRISIS_LLM:
        return senal_debil(actual)
    try:
        return await llm.clasificar_crisis(mensajes)
    except Exception:
        logger.exception("Falló el clasificador de crisis; se usa solo la lista de señales")
        return senal_debil(actual)


# --- Validador de salida ---------------------------------------------------
_URL = re.compile(r"https?://\S+")
_SIGNOS = [
    ("comillas", re.compile(r"[\"“”«»‘’]")),
    ("puntos suspensivos", re.compile(r"\.\.\.|…")),
    ("guiones", re.compile(r"[-—–]")),
    ("dos puntos", re.compile(r"(?<!\d):|:(?!\d)")),
    ("punto y coma", re.compile(r";")),
]
_MARKDOWN = re.compile(r"\*|__|`|^\s*(#{1,6}\s|[•]\s?|\d+[.)]\s)", re.MULTILINE)
# El prompt pide no asumir el género; los evals mostraron "juntos/juntas" en 38 % de las conversaciones
_GENERO_PROHIBIDO = re.compile(r"\b(juntos|juntas)\b", re.IGNORECASE)
_PALABRAS_PROHIBIDAS = re.compile(
    r"\b(terapi\w*|terapeut\w*|psicolog[oa]s?|pacientes?|tratamientos?|diagnostic\w*"
    r"|trastornos?|patologi\w*|clinic[oa]s?|consultas? psicologicas?)\b"
)
_MONTO = re.compile(r"(\$\s?)?(\d[\d,]*(?:\.\d+)?)\s*(pesos|mxn|usd|dolares|dlls|euros?|soles|bolivares|cop)?\b")
_PERMITIDOS = {"pesos": {300, 136}, "usd": {8}}
_MONEDA = {"$": "pesos", "pesos": "pesos", "mxn": "pesos", "usd": "usd", "dolares": "usd", "dlls": "usd"}


def _montos_invalidos(texto: str) -> list[str]:
    invalidos = []
    for m in _MONTO.finditer(texto):
        signo, numero, moneda = m.groups()
        if not signo and not moneda:
            continue  # número sin moneda: teléfono, hora, cantidad
        clave = _MONEDA.get(moneda or "$")
        valor = float(numero.replace(",", ""))
        if clave is None or valor not in _PERMITIDOS[clave]:
            invalidos.append(m.group(0).strip())
    return invalidos


def es_problema_de_largo(problema: str) -> bool:
    """Reglas de estilo que no justifican mandar el respaldo (ver main.generar_validada)."""
    return problema.startswith(("demasiado largo", "demasiados párrafos"))


def validar_salida(texto: str) -> list[str]:
    """Lista de reglas incumplidas; vacía si el mensaje puede enviarse."""
    if not texto.strip():
        return ["vacío"]
    problemas = []
    sin_urls = _URL.sub(" ", texto)
    problemas += [nombre for nombre, patron in _SIGNOS if patron.search(sin_urls)]
    if _MARKDOWN.search(sin_urls):
        problemas.append("markdown o lista")
    if MARCA_EDUARDO.strip().lower() in sin_urls.lower():
        problemas.append("se hace pasar por Eduardo")
    if MARCA_AUDIO.strip().lower() in sin_urls.lower():
        problemas.append("marca interna de nota de voz")
    genero = _GENERO_PROHIBIDO.search(sin_urls)
    if genero:
        problemas.append(f"asume género con '{genero.group(0)}' (usa una frase neutra como en conjunto)")

    normal = normalizar(sin_urls)
    palabras = sorted({m.group(0) for m in _PALABRAS_PROHIBIDAS.finditer(normal)})
    problemas += [f"palabra prohibida {p}" for p in palabras]
    problemas += [f"precio no autorizado {m}" for m in _montos_invalidos(normal)]

    if len(texto) > config.MAX_CARACTERES:
        problemas.append(f"demasiado largo ({len(texto)} caracteres)")
    parrafos = [p for p in re.split(r"\n\s*\n", texto) if p.strip()]
    if len(parrafos) > config.MAX_PARRAFOS:
        problemas.append(f"demasiados párrafos ({len(parrafos)})")
    return problemas
