import re
from datetime import datetime
from pathlib import Path

from app import config
from app.scheduling import calcular_horarios

RUTA_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "sofia.md"
_MARCADOR = re.compile(r"\{\{(\w+)\}\}")


def renderizar(plantilla: str, valores: dict) -> str:
    """Sustituye {{variable}}. Falla si falta alguna, para no enviar un prompt roto."""
    faltantes = set(_MARCADOR.findall(plantilla)) - set(valores)
    if faltantes:
        raise KeyError(f"Variables sin valor en el prompt: {sorted(faltantes)}")
    return _MARCADOR.sub(lambda m: str(valores[m.group(1)]), plantilla)


def cargar_prompt(ahora: datetime | None = None) -> str:
    ahora = ahora or datetime.now(config.ZONA_MX)
    valores = {
        "fecha_hora": ahora.strftime("%A %d/%m/%Y %I:%M %p"),
        "opciones_horario": calcular_horarios(ahora),
        **config.DATOS_PAGO,
    }
    return renderizar(RUTA_PROMPT.read_text(encoding="utf-8"), valores)
