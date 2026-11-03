import hashlib
import re


def normalizar_numero(numero: str) -> str:
    """Meta entrega números MX como 521XXXXXXXXXX; para enviar se usa 52XXXXXXXXXX."""
    if numero.startswith("521") and len(numero) == 13:
        return "52" + numero[3:]
    return numero


def limpiar_texto(texto: str) -> str:
    """Quita signos que el prompt prohíbe por si el modelo los genera igual."""
    texto = re.sub(r'["“”«»]', "", texto)
    texto = texto.replace("…", ".").replace("...", ".")
    texto = re.sub(r"\s[—–-]\s", ", ", texto)
    texto = texto.replace(": ", ", ").replace("; ", ", ")
    # "juntos" asume el género. Claude lo repite aun corregido y rechazarlo terminaba en el respaldo,
    # así que se reemplaza por la frase neutra que pide el prompt.
    texto = re.sub(r"\b(juntos|juntas)\b", "en conjunto", texto)
    texto = re.sub(r"\b(Juntos|Juntas)\b", "En conjunto", texto)
    return texto.strip()


def huella(texto: str) -> str:
    """Identificador corto para logs sin exponer el contenido."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:10]
