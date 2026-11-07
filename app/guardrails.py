"""Guardrails deterministas (Poka-yoke): no dependen de que el modelo obedezca el prompt."""

import logging
import re
import unicodedata

from app import config, llm

logger = logging.getLogger("sofia.guardrails")

LINEA_DE_LA_VIDA = "800 911 2000"

# Líneas de ayuda por país, con fuente revisada el 5 de noviembre de 2026. El modelo nunca las escribe. Un país sin
# entrada (fuentes contradictorias o sin confirmar) recibe el texto genérico: mejor eso que un número equivocado.
LINEAS_DE_AYUDA = {
    "México": f"la Línea de la Vida al {LINEA_DE_LA_VIDA}, gratuita y disponible las 24 horas",
    "Estados Unidos": "el 988 por llamada o texto, las 24 horas. En español marca 988 y oprime 2, "
    "o manda AYUDA por texto al 988",
    "Puerto Rico": "la Línea PAS al 1 800 981 0023 o al 988",
    "España": "la línea 024, gratuita y confidencial. En una emergencia vital llama al 112",
    "Chile": "la línea *4141, gratuita y disponible las 24 horas",
    "Argentina": "el Centro de Asistencia al Suicida al 135 o al 011 5275 1135",
    "Uruguay": "la Línea Vida al 0800 0767, o al *0767 desde celular",
    "Ecuador": "la línea 171 opción 6, de 7 AM a 8 PM",
    "Perú": "la línea 113 del Minsa y pide orientación en salud mental, gratuita y disponible las 24 horas",
    "Colombia": "la línea 106 si estás en Bogotá. En una emergencia llama al 123",
}
# Prefijo telefónico → país; gana el más largo. None: comparte el +1 pero no se usa la línea de Estados Unidos.
_PAIS_POR_PREFIJO = {
    "52": "México",
    "1": "Estados Unidos",
    "1787": "Puerto Rico",
    "1939": "Puerto Rico",
    "1809": None,
    "1829": None,
    "1849": None,
    "34": "España",
    "56": "Chile",
    "54": "Argentina",
    "598": "Uruguay",
    "593": "Ecuador",
    "51": "Perú",
    "57": "Colombia",
}
# Países que Sofía reconoce al preguntar. Los que no tienen línea reciben el texto genérico.
_PAISES = re.compile(
    r"\b(mexico|estados unidos|usa|eeuu|ee uu|united states|puerto rico|espana|chile|argentina|uruguay|ecuador|peru"
    r"|colombia|venezuela|bolivia|paraguay|brasil|guatemala|honduras|el salvador|nicaragua|costa rica|panama|cuba"
    r"|republica dominicana|canada)\b"
)
_NOMBRE_DE_PAIS = {
    "mexico": "México",
    "estados unidos": "Estados Unidos",
    "usa": "Estados Unidos",
    "eeuu": "Estados Unidos",
    "ee uu": "Estados Unidos",
    "united states": "Estados Unidos",
    "puerto rico": "Puerto Rico",
    "espana": "España",
    "peru": "Perú",
}

_INICIO_CRISIS = "Lo que me cuentas es muy serio"
_INICIO_PAUSA = "Te leo."
_PELIGRO = "Si estás en peligro inmediato, ve al hospital más cercano o llama a emergencias."
_RED_DE_APOYO = (
    "Busca ahora a un familiar o a alguien de confianza, cuéntale cómo te sientes y pídele que se quede contigo. "
    "Te recomiendo acudir en persona a atención médica, psicológica o psiquiátrica."
)
_NO_SOMOS_INDICADOS = (
    "Este servicio es coaching de vida, no atención psicológica, psiquiátrica ni de emergencias. Por lo que me "
    "cuentas, en este momento no somos las personas indicadas para acompañarte y no podemos agendarte una sesión."
)
# Solo cuenta este texto exacto, en mayúsculas. VERSION_AVISO queda en el registro: cambiarla si cambia el aviso.
PALABRA_ACEPTAR = "ACEPTO"
VERSION_AVISO = "2026-11-05"
_PIDE_ACEPTAR = (
    "Si lo que vives no es una crisis, entiendes que esto es coaching de vida y aun así quieres continuar, "
    f"escribe solamente {PALABRA_ACEPTAR} en mayúsculas."
)
RESPUESTA_ACEPTADO = "Gracias, quedó registrado. ¿Te gustaría agendar una sesión?"
_PREGUNTA_PAIS = "¿En qué país estás? Así te paso la línea de ayuda que te corresponde."


def _pais_del_telefono(telefono: str) -> str | None:
    prefijo = max((p for p in _PAIS_POR_PREFIJO if telefono.startswith(p)), key=len, default=None)
    return _PAIS_POR_PREFIJO.get(prefijo)


def pais_en_texto(texto: str) -> str | None:
    encontrado = _PAISES.search(normalizar(texto))
    if not encontrado:
        return None
    return _NOMBRE_DE_PAIS.get(encontrado.group(1), encontrado.group(1).title())


def _linea_de(telefono: str) -> str:
    pais = _pais_del_telefono(telefono)
    return f"Si estás en {pais}, llama a {LINEAS_DE_AYUDA[pais]}. " if pais else ""


def _linea_segun(telefono: str, texto: str) -> str:
    """Lo que dice la persona manda sobre su prefijo. Si ya dijo su país no se le vuelve a preguntar."""
    pais = pais_en_texto(texto) if texto else None
    if pais in LINEAS_DE_AYUDA:
        return f"En {pais} puedes llamar a {LINEAS_DE_AYUDA[pais]}."
    if pais:
        return f"En {pais} llama a tu número local de emergencias."
    return f"{_linea_de(telefono)}{_PREGUNTA_PAIS}"


def _aviso(telefono: str, texto: str = "") -> str:
    return f"{_NO_SOMOS_INDICADOS}\n\n{_RED_DE_APOYO} {_PELIGRO}\n\n{_linea_segun(telefono, texto)}\n\n{_PIDE_ACEPTAR}"


def respuesta_crisis(telefono: str) -> str:
    return f"{_INICIO_CRISIS} y lo más importante ahora es que estés a salvo. {_aviso(telefono)}"


def respuesta_pausa(telefono: str, texto: str = "") -> str:
    return f"{_INICIO_PAUSA} {_aviso(telefono, texto)}"


RESPUESTA_CRISIS = respuesta_crisis("52")
RESPUESTA_PAUSA = respuesta_pausa("52")

# Prefijo con el que se guardan en el historial los mensajes que Eduardo escribe desde Telegram.
MARCA_EDUARDO = "[Eduardo] "
# Prefijo de las notas de voz transcritas (la transcripción automática puede tener errores).
MARCA_AUDIO = "[nota de voz] "

RESPUESTA_AUDIO_FALLIDO = "Recibí tu nota de voz, pero no logré escucharla bien. ¿Me lo puedes escribir por aquí?"

RESPUESTA_DERIVACION = "Gracias por contarme. Eduardo te escribirá personalmente a la brevedad."

RESPUESTA_NO_COMPROBANTE = (
    "Eduardo revisó tu archivo y no parece un comprobante de pago. Si ya pagaste, mándalo de nuevo por aquí, por favor."
)
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
    "Sanando el Alma es un servicio profesional de coaching de vida. No sustituye la terapia psicológica ni la "
    "atención de salud.\n"
    "En sesiones privadas con Eduardo, por escrito y audios en vivo, trabajas para entender de dónde viene lo que "
    "te pasa, ver con claridad el patrón que repites y reaccionar menos ante él.\n\n"
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
    r"suicid|quitarme la vida|matarme"
    r"|quiero morir(?! de (la |el )?(risa|verguenza|pena|hambre|sueno|ganas|amor|calor|frio))"
    r"|no quiero (seguir )?vivi(r|endo)"
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


def _es_respuesta_de_crisis(mensaje: dict) -> bool:
    return mensaje["role"] == "assistant" and mensaje["content"].startswith((_INICIO_CRISIS, _INICIO_PAUSA))


def hubo_crisis(historial: list[dict]) -> bool:
    return any(_es_respuesta_de_crisis(m) for m in historial)


def mensajes_para_crisis(historial: list[dict], texto: str) -> list[str]:
    """Lo que lee el clasificador: los mensajes de la persona posteriores a la última respuesta fija de crisis y,
    primero, el final del último mensaje de Sofía.

    La crisis anterior ya se atendió: si un mensaje llega hasta aquí es que se reanudó la conversación, y releerla
    haría que un simple "hola" volviera a pausarla. El mensaje de Sofía da el contexto de un "sí" o un "no" a una
    pregunta como si sigue pensando en quitarse la vida."""
    corte = max((i for i, m in enumerate(historial) if _es_respuesta_de_crisis(m)), default=-1)
    recientes = [m["content"] for m in historial[corte + 1 :] if m["role"] == "user"][-3:]
    recientes = recientes if texto in recientes else [*recientes[-2:], texto]
    de_sofia = [m["content"] for m in historial if m["role"] == "assistant"]
    return [f"{llm.MARCA_SOFIA}{de_sofia[-1][-300:]}", *recientes] if de_sofia else recientes


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


# 16 o más dígitos seguidos, con espacios o guiones sueltos: una CLABE (18) o una tarjeta (16). Un teléfono
# internacional tiene 15 como máximo, así que no se confunde.
_NUMERO_DE_CUENTA = re.compile(r"\d(?:[ -]?\d){15,}")
_PUNTUACION_DE_ENLACE = ".,;:!?)]}»”\"'"


def _enlace_limpio(enlace: str) -> str:
    """El enlace sin la puntuación o la barra final que la frase le pega."""
    return enlace.strip().rstrip(_PUNTUACION_DE_ENLACE + "/")


def _datos_de_pago_ajenos(texto: str) -> list[str]:
    """Una CLABE, tarjeta o enlace que no sea exactamente el configurado. El modelo puede copiarlo de un mensaje
    de la persona ("paga a esta cuenta"), inventarlo o equivocarse: lo que sale de este número no debe mandar
    dinero a otro lado. Sin dato configurado no se acepta ninguno. Los avisos no repiten el número ni el enlace."""
    problemas = []
    clabe = re.sub(r"\D", "", config.DATOS_PAGO["clabe"])
    for numero in _NUMERO_DE_CUENTA.finditer(texto):
        if not clabe or re.sub(r"\D", "", numero.group(0)) != clabe:
            problemas.append("número de cuenta o tarjeta distinto del configurado")
            break
    permitidos = {
        _enlace_limpio(enlace)
        for enlace in (config.DATOS_PAGO["link_stripe"], config.DATOS_PAGO["link_guia"], config.URL_PRIVACIDAD)
        if enlace
    }
    if any(_enlace_limpio(enlace) not in permitidos for enlace in _URL.findall(texto)):
        problemas.append("enlace que no es de los configurados")
    return problemas


def es_problema_de_largo(problema: str) -> bool:
    """Reglas de estilo que no justifican mandar el respaldo (ver main.generar_validada)."""
    return problema.startswith(("demasiado largo", "demasiados párrafos"))


def validar_salida(texto: str) -> list[str]:
    """Lista de reglas incumplidas; vacía si el mensaje puede enviarse."""
    if not texto.strip():
        return ["vacío"]
    problemas = _datos_de_pago_ajenos(texto)
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
