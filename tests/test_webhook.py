"""Rutas HTTP con `procesar_mensaje` reemplazado: solo se prueba qué se encola."""

import pytest
from fastapi.testclient import TestClient

from app import main
from tests.conftest import payload, post_firmado


@pytest.fixture
def cliente(monkeypatch):
    procesados = []

    async def falso_procesar(remitente, texto, wa_id):
        procesados.append((remitente, texto, wa_id))

    monkeypatch.setattr(main, "procesar_mensaje", falso_procesar)
    c = TestClient(main.app)
    c.procesados = procesados
    return c


def test_health(cliente):
    assert cliente.get("/health").json() == {"status": "ok"}


def test_verificacion_ok(cliente):
    r = cliente.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "tok", "hub.challenge": "42"})
    assert r.status_code == 200 and r.text == "42"


@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "42"},
        {"hub.mode": "unsubscribe", "hub.verify_token": "tok", "hub.challenge": "42"},
        {},
    ],
)
def test_verificacion_rechazada(cliente, params):
    assert cliente.get("/webhook", params=params).status_code == 403


def test_firma_invalida_se_rechaza(cliente):
    r = post_firmado(cliente, payload(), firma="sha256=deadbeef")
    assert r.status_code == 401
    assert cliente.procesados == []


def test_sin_firma_se_rechaza(cliente):
    r = cliente.post("/webhook", json=payload())
    assert r.status_code == 401


def test_json_invalido_con_firma_valida(cliente):
    assert post_firmado(cliente, b"{no es json").status_code == 400


def test_texto_se_encola(cliente):
    r = post_firmado(cliente, payload(texto="Necesito ayuda"))
    assert r.json() == {"status": "recibido"}
    assert cliente.procesados == [("5214931234567", "Necesito ayuda", "wamid.ABC")]


def test_evento_de_estado_se_ignora(cliente):
    datos = {"entry": [{"changes": [{"value": {"statuses": [{"status": "read"}]}}]}]}
    assert post_firmado(cliente, datos).json() == {"status": "ignorado"}
    assert cliente.procesados == []


@pytest.mark.parametrize("tipo", ["sticker", "audio", "image", "reaction"])
def test_tipo_no_soportado_se_ignora(cliente, tipo):
    assert post_firmado(cliente, payload(tipo=tipo)).json() == {"status": "ignorado"}
    assert cliente.procesados == []


def test_texto_vacio_se_ignora(cliente):
    assert post_firmado(cliente, payload(texto="")).json() == {"status": "ignorado"}
    assert cliente.procesados == []
