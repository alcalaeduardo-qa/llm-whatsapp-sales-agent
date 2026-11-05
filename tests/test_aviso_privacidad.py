"""Día 9: aviso de privacidad al pie de la primera respuesta de Sofía."""

import httpx
import pytest

from app import config, guardrails
from tests.conftest import payload, post_firmado
from tests.falsos import SALUDO as SALUDO_CRUDO
from tests.falsos import _enviados

TEL = "5214931234567"
SALUDO = SALUDO_CRUDO.replace(":", ",")  # limpiar_texto quita los dos puntos
URL = "https://ejemplo.test/privacidad"


@pytest.fixture
def sin_aviso(entorno, monkeypatch):
    monkeypatch.setattr(config, "URL_PRIVACIDAD", URL)
    entorno["store"].aviso_ya_enviado = False
    return entorno


def test_la_primera_respuesta_lleva_el_enlace_y_el_aviso_de_borrado(sin_aviso):
    post_firmado(sin_aviso["http"], payload(texto="Hola", wa_id="w1"))

    (enviado,) = _enviados(sin_aviso["meta"])
    assert enviado.startswith(SALUDO)
    assert URL in enviado
    assert "90 días" in enviado and "sanandoalmaa@gmail.com" in enviado


def test_el_aviso_dice_cuando_se_borra_la_conversacion_y_como_pedirlo_antes():
    assert "90 días" in guardrails.AVISO_PRIVACIDAD
    assert "sanandoalmaa@gmail.com" in guardrails.AVISO_PRIVACIDAD
    assert "BORRAR MIS DATOS" not in guardrails.AVISO_PRIVACIDAD  # el comando ya no existe


def test_el_aviso_se_manda_una_sola_vez(sin_aviso):
    post_firmado(sin_aviso["http"], payload(texto="Hola", wa_id="w1"))
    post_firmado(sin_aviso["http"], payload(texto="Cuéntame más", wa_id="w2"))

    assert _enviados(sin_aviso["meta"])[1] == SALUDO


def test_el_aviso_no_entra_al_historial_de_claude(sin_aviso):
    post_firmado(sin_aviso["http"], payload(texto="Hola", wa_id="w1"))

    assert sin_aviso["store"].respuestas_a(TEL) == [SALUDO]


def test_la_crisis_no_lleva_aviso_y_llega_cuando_sofia_retoma(sin_aviso):
    sin_aviso["claude"].veredicto = "RIESGO"
    post_firmado(sin_aviso["http"], payload(texto="ya no aguanto", wa_id="w1"))
    assert URL not in _enviados(sin_aviso["meta"])[0]

    sin_aviso["claude"].veredicto = "NORMAL"
    sin_aviso["store"].filas.clear()
    sin_aviso["store"].pausadas.clear()
    post_firmado(sin_aviso["http"], payload(texto="Ya estoy mejor", wa_id="w2"))

    assert URL in _enviados(sin_aviso["meta"])[-1]


def test_si_no_se_pudo_enviar_el_aviso_queda_pendiente(sin_aviso):
    sin_aviso["meta"].mock(return_value=httpx.Response(500, json={}))

    post_firmado(sin_aviso["http"], payload(texto="Hola", wa_id="w1"))

    assert sin_aviso["store"].avisados == []
