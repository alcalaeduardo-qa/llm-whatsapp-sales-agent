import logging
import re
from datetime import datetime
from pathlib import Path

from app import config
from app.scheduling import calcular_horarios, fecha_hora_legible

logger = logging.getLogger("sofia.prompt")

_DIR = Path(__file__).resolve().parent.parent / "prompts"
RUTA_PROMPT = _DIR / "sofia.md"  # prompt real, fuera de git
RUTA_EJEMPLO = _DIR / "sofia.example.md"  # plantilla pública para clones y CI
_MARCADOR = re.compile(r"\{\{(\w+)\}\}")
_avisado = False


def ruta_prompt() -> Path:
    """El prompt real si existe; si no, la plantilla de ejemplo (con aviso, una sola vez)."""
    global _avisado
    if RUTA_PROMPT.exists():
        return RUTA_PROMPT
    if not _avisado:
        logger.warning("No existe %s; se usa el prompt de ejemplo", RUTA_PROMPT.name)
        _avisado = True
    return RUTA_EJEMPLO


def renderizar(plantilla: str, valores: dict) -> str:
    """Sustituye {{variable}}. Falla si falta alguna, para no enviar un prompt roto."""
    faltantes = set(_MARCADOR.findall(plantilla)) - set(valores)
    if faltantes:
        raise KeyError(f"Variables sin valor en el prompt: {sorted(faltantes)}")
    return _MARCADOR.sub(lambda m: str(valores[m.group(1)]), plantilla)


def cargar_prompt(ahora: datetime | None = None) -> str:
    ahora = ahora or datetime.now(config.ZONA_MX)
    valores = {
        "fecha_hora": fecha_hora_legible(ahora),
        "opciones_horario": calcular_horarios(ahora),
        **config.DATOS_PAGO,
    }
    return renderizar(ruta_prompt().read_text(encoding="utf-8"), valores)
