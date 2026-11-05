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


def test_la_primera_respuesta_lleva_el_enlace_y_el_comando_de_borrado(sin_aviso):
    post_firmado(sin_aviso["http"], payload(texto="Hola", wa_id="w1"))

    (enviado,) = _enviados(sin_aviso["meta"])
    assert enviado.startswith(SALUDO)
    assert URL in enviado
    assert "BORRAR MIS DATOS" in enviado


def test_el_comando_del_aviso_es_el_que_borra_de_verdad():
    assert guardrails.es_borrar_mis_datos("BORRAR MIS DATOS")
    assert "BORRAR MIS DATOS" in guardrails.AVISO_PRIVACIDAD


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


def test_quien_borra_sus_datos_recibe_el_aviso_otra_vez(sin_aviso):
    post_firmado(sin_aviso["http"], payload(texto="Hola", wa_id="w1"))
    post_firmado(sin_aviso["http"], payload(texto="BORRAR MIS DATOS", wa_id="w2"))
    post_firmado(sin_aviso["http"], payload(texto="Hola de nuevo", wa_id="w3"))

    assert URL in _enviados(sin_aviso["meta"])[-1]
