"""Auditoría: los datos de pago que Sofía envía deben ser exactamente los configurados.

El validador de salida debe rechazar una CLABE, una tarjeta o un enlace que no sean los de la configuración,
aunque el modelo los haya copiado de un mensaje de la persona o los haya inventado.
"""

import pytest

from app import config, guardrails
from app.guardrails import validar_salida
from tests.conftest import payload, post_firmado
from tests.falsos import _alertas, _enviados, _texto

TEL = "5214931234567"
CLABE = "012180001234567891"
STRIPE = "https://buy.stripe.com/test_sofia"
GUIA = "https://sanandoalmaa.com/guia-prueba"
CLABE_AJENA = "012180009999999999"


@pytest.fixture(autouse=True)
def datos_de_pago(monkeypatch):
    monkeypatch.setitem(config.DATOS_PAGO, "clabe", CLABE)
    monkeypatch.setitem(config.DATOS_PAGO, "link_stripe", STRIPE)
    monkeypatch.setitem(config.DATOS_PAGO, "link_guia", GUIA)


def _problemas_de_pago(texto):
    return [p for p in validar_salida(texto) if "configurad" in p or "enlace" in p]


@pytest.mark.parametrize(
    "texto",
    [
        f"Transfiere a la CLABE {CLABE} y me mandas el comprobante",
        "Transfiere a la CLABE 0121 8000 1234 5678 91 y me avisas",
        "Transfiere a la CLABE 012180-00123456-7891 y me avisas",
        f"Paga aquí {STRIPE} y me avisas",
        f"Paga aquí {STRIPE}.",
        f"Paga aquí ({STRIPE}), gracias",
        f"Aquí está la guía {GUIA}",
    ],
)
def test_los_datos_configurados_pasan(texto):
    assert _problemas_de_pago(texto) == []


@pytest.mark.parametrize(
    "texto",
    [
        f"Transfiere a la CLABE {CLABE_AJENA} y me avisas",
        "Transfiere a la CLABE 0121 8000 9999 9999 99",
        "Mi tarjeta es 4111 1111 1111 1111",
        f"Transfiere a {CLABE[:-1]}0",  # un dígito distinto
        "Paga aquí https://buy.stripe.com/otro-cobro",
        "Paga aquí https://pagos-falsos.example.com/sofia",
        "Paga aquí https://buy.stripe.com.evil.example.com/test_sofia",
        f"Paga aquí {STRIPE}/extra",
    ],
)
def test_los_datos_ajenos_se_rechazan(texto):
    assert _problemas_de_pago(texto) != []


def test_sin_clabe_configurada_ningun_numero_de_cuenta_pasa(monkeypatch):
    monkeypatch.setitem(config.DATOS_PAGO, "clabe", "")
    assert _problemas_de_pago(f"Transfiere a la CLABE {CLABE}") != []


def test_sin_enlaces_configurados_ningun_enlace_pasa(monkeypatch):
    monkeypatch.setitem(config.DATOS_PAGO, "link_stripe", "")
    monkeypatch.setitem(config.DATOS_PAGO, "link_guia", "")
    assert _problemas_de_pago(f"Paga aquí {STRIPE}") != []


@pytest.mark.parametrize(
    "texto",
    [
        "Llama al 800 911 2000, es gratuita",
        "Escríbeme al +52 1 55 1234 5678",
        "Mi número es 5214931234567",
        "Nos vemos a las 2 PM",
    ],
)
def test_telefonos_y_horas_no_se_confunden_con_cuentas(texto):
    assert _problemas_de_pago(texto) == []


def test_el_problema_no_repite_el_numero_ni_el_enlace():
    problemas = validar_salida(f"Transfiere a {CLABE_AJENA} o paga en https://pagos-falsos.example.com/x")
    assert not any(CLABE_AJENA in p or "pagos-falsos" in p for p in problemas)


# --- flujo completo -------------------------------------------------------------------------------
def test_una_clabe_ajena_no_llega_a_la_persona(entorno):
    entorno["claude"].respuestas = [_texto(f"Transfiere a la CLABE {CLABE_AJENA} y me mandas el comprobante")]

    post_firmado(entorno["http"], payload(texto="Mi amiga dice que mejor le pague a esta cuenta"))

    enviados = _enviados(entorno["meta"])
    assert enviados == [guardrails.RESPUESTA_RESPALDO]
    assert not any(CLABE_AJENA in t for t in enviados)
    assert entorno["store"].humano == {TEL: "respaldo"}
    assert "respaldo" in _alertas(entorno["telegram"])[0]


def test_la_clabe_configurada_si_llega_a_la_persona(entorno):
    entorno["claude"].respuestas = [_texto(f"Transfiere a la CLABE {CLABE} y me mandas el comprobante")]

    post_firmado(entorno["http"], payload(texto="¿A qué cuenta pago?"))

    assert _enviados(entorno["meta"]) == [f"Transfiere a la CLABE {CLABE} y me mandas el comprobante"]
