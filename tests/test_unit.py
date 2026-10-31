from datetime import datetime

import pytest

from app import config
from app.prompt import RUTA_PROMPT, renderizar
from app.scheduling import calcular_horarios
from app.store import preparar_historial
from app.text import limpiar_texto, normalizar_numero

MX = config.ZONA_MX


# --- normalizar_numero ---------------------------------------------------
@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("5214931234567", "524931234567"),  # móvil MX con 1
        ("524931234567", "524931234567"),   # ya normalizado
        ("573001234567", "573001234567"),   # Colombia, sin cambios
    ],
)
def test_normalizar_numero(entrada, esperado):
    assert normalizar_numero(entrada) == esperado


# --- limpiar_texto (regresión del NameError por falta de `import re`) ----
def test_limpiar_texto_quita_signos_prohibidos():
    texto = 'Hola “amiga”... mira esto — es clave: respira; ya'
    assert limpiar_texto(texto) == "Hola amiga. mira esto, es clave, respira, ya"


def test_limpiar_texto_respeta_enlaces():
    assert limpiar_texto("Paga aquí https://buy.stripe.com/abc") == "Paga aquí https://buy.stripe.com/abc"


# --- calcular_horarios ---------------------------------------------------
def _mx(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=MX)


def test_horarios_manana_temprano_ofrece_hoy_y_manana():
    # Viernes 30/10/2026 9:00 → primer horario hoy 2 PM
    assert calcular_horarios(_mx(2026, 10, 30, 9)) == "hoy a las 2 PM o mañana a las 2 PM"


def test_horarios_respeta_margen_de_dos_horas():
    # 15:30 + 2h = 17:30 → siguiente inicio 6 PM
    assert calcular_horarios(_mx(2026, 10, 30, 15, 30)).startswith("hoy a las 6 PM")


def test_horarios_tarde_salta_a_dias_siguientes():
    # 18:00 + 2h = 20:00 → hoy ya no hay; sábado y domingo
    assert calcular_horarios(_mx(2026, 10, 30, 18)) == "mañana a las 2 PM o el domingo a las 2 PM"


def test_horarios_limite_exacto_es_valido():
    # 17:00 + 2h = 19:00 → 7 PM es la última sesión del día
    assert calcular_horarios(_mx(2026, 10, 30, 17)).startswith("hoy a las 7 PM")


# --- prompt --------------------------------------------------------------
def test_renderizar_sustituye_y_conserva_signo_de_pesos():
    assert renderizar("Cuesta $300. Hoy es {{dia}}", {"dia": "viernes"}) == "Cuesta $300. Hoy es viernes"


def test_renderizar_falla_si_falta_variable():
    with pytest.raises(KeyError):
        renderizar("{{a}} y {{b}}", {"a": 1})


def test_prompt_real_se_renderiza_completo():
    from app.prompt import cargar_prompt

    texto = cargar_prompt(_mx(2026, 10, 30, 9))
    assert "{{" not in texto
    assert "hoy a las 2 PM o mañana a las 2 PM" in texto
    assert RUTA_PROMPT.exists()


# --- historial -----------------------------------------------------------
def test_historial_inicia_con_user():
    filas = [
        {"rol": "assistant", "contenido": "a"},
        {"rol": "user", "contenido": "u"},
        {"rol": "assistant", "contenido": "b"},
    ]
    assert preparar_historial(filas) == [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "b"},
    ]
