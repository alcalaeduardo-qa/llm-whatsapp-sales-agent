"""Auditoría, pistas 17 y 18: sin tope, una persona podía agotar el crédito de Claude o llenar el Telegram de
Eduardo y enterrar las alertas de crisis."""

import pytest

from app import config, guardrails, limites
from tests.conftest import payload, post_firmado
from tests.falsos import _alertas, _enviados

TEL = "5214931234567"


@pytest.fixture(autouse=True)
def limpio():
    limites.reiniciar()


@pytest.fixture
def tope(monkeypatch):
    monkeypatch.setattr(config, "LIMITE_MENSAJES_HORA", 2)


def test_bajo_el_tope_se_atiende():
    assert limites.revisar(TEL, ahora=0) is None


def test_el_tope_por_persona_se_libera_al_pasar_la_hora(tope):
    limites.revisar(TEL, ahora=0)
    limites.revisar(TEL, ahora=1)

    assert limites.revisar(TEL, ahora=2) == "persona"
    assert limites.revisar(TEL, ahora=limites.HORA + 1) is None


def test_el_tope_de_una_persona_no_frena_a_otra(tope):
    limites.revisar(TEL, ahora=0)
    limites.revisar(TEL, ahora=1)

    assert limites.revisar("5215550000000", ahora=2) is None


def test_el_tope_total_del_dia(monkeypatch):
    monkeypatch.setattr(config, "LIMITE_MENSAJES_DIA", 2)
    limites.revisar("1", ahora=0)
    limites.revisar("2", ahora=1)

    assert limites.revisar("3", ahora=2) == "total"


def test_el_aviso_sale_una_vez_por_ventana():
    assert limites.primer_aviso(TEL, limites.HORA, ahora=0)
    assert not limites.primer_aviso(TEL, limites.HORA, ahora=10)
    assert limites.primer_aviso(TEL, limites.HORA, ahora=limites.HORA)


def test_pasado_el_tope_no_responde_claude_y_avisa_una_sola_vez(entorno, tope):
    for i in range(5):
        post_firmado(entorno["http"], payload("text", f"hola {i}", f"wamid.{i}"))

    assert len(entorno["claude"].peticiones) == 2  # solo los 2 mensajes bajo el tope
    assert _enviados(entorno["meta"]).count(guardrails.RESPUESTA_LIMITE) == 1
    assert sum("mensajes en una hora" in a for a in _alertas(entorno["telegram"])) == 1


def test_en_modo_humano_el_tope_frena_el_reenvio_a_telegram(entorno, tope, monkeypatch):
    async def sin_riesgo(*args, **kwargs):
        return False

    monkeypatch.setattr(guardrails, "detectar_crisis", sin_riesgo)
    entorno["store"].humano[TEL] = "eduardo_responde"
    for i in range(6):
        post_firmado(entorno["http"], payload("text", f"spam {i}", f"wamid.{i}"))

    reenviados = [a for a in _alertas(entorno["telegram"]) if "spam" in a]
    assert len(reenviados) == 2


def test_pasado_el_tope_una_crisis_explicita_si_recibe_la_linea(entorno, tope):
    for i in range(2):
        post_firmado(entorno["http"], payload("text", f"hola {i}", f"wamid.{i}"))

    post_firmado(entorno["http"], payload("text", "quiero morir", "wamid.9"))

    assert guardrails.respuesta_crisis(TEL) in _enviados(entorno["meta"])
