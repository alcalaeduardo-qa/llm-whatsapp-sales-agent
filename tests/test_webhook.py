import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app import main, whatsapp

SECRETO = "secreto-de-prueba"


def _firmar(cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).hexdigest()


def _payload(tipo="text", texto="Hola"):
    msg = {"from": "5214931234567", "id": "wamid.ABC", "type": tipo}
    if tipo == "text":
        msg["text"] = {"body": texto}
    return {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(whatsapp.config, "APP_SECRET", SECRETO)
    monkeypatch.setattr(main.config, "VERIFY_TOKEN", "tok")
    procesados = []

    async def falso_procesar(remitente, texto, wa_id):
        procesados.append((remitente, texto, wa_id))

    monkeypatch.setattr(main, "procesar_mensaje", falso_procesar)
    c = TestClient(main.app)
    c.procesados = procesados
    return c


def _post(cliente, payload, firma=None):
    cuerpo = json.dumps(payload).encode()
    return cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Hub-Signature-256": firma or _firmar(cuerpo), "Content-Type": "application/json"},
    )


def test_verificacion_ok(cliente):
    r = cliente.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "tok", "hub.challenge": "42"})
    assert r.status_code == 200 and r.text == "42"


def test_verificacion_token_incorrecto(cliente):
    r = cliente.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "42"})
    assert r.status_code == 403


def test_firma_invalida_se_rechaza(cliente):
    r = _post(cliente, _payload(), firma="sha256=deadbeef")
    assert r.status_code == 401
    assert cliente.procesados == []


def test_sin_firma_se_rechaza(cliente):
    r = cliente.post("/webhook", json=_payload())
    assert r.status_code == 401


def test_texto_se_encola(cliente):
    r = _post(cliente, _payload(texto="Necesito ayuda"))
    assert r.json() == {"status": "recibido"}
    assert cliente.procesados == [("5214931234567", "Necesito ayuda", "wamid.ABC")]


def test_evento_de_estado_se_ignora(cliente):
    payload = {"entry": [{"changes": [{"value": {"statuses": [{"status": "read"}]}}]}]}
    assert _post(cliente, payload).json() == {"status": "ignorado"}


def test_tipo_no_soportado_se_ignora(cliente):
    assert _post(cliente, _payload(tipo="sticker")).json() == {"status": "ignorado"}
    assert cliente.procesados == []


# --- procesar_mensaje con dependencias falsas ---------------------------
@pytest.mark.anyio
async def test_procesar_duplicado_no_llama_a_claude(monkeypatch):
    llamadas = []

    async def registrar(*a):
        return False

    async def generar(*a):
        llamadas.append("claude")
        return "x"

    monkeypatch.setattr(main.store, "registrar_entrante", registrar)
    monkeypatch.setattr(main.llm, "generar_respuesta", generar)
    await main.procesar_mensaje("521", "hola", "wamid.1")
    assert llamadas == []


@pytest.mark.anyio
async def test_procesar_flujo_completo(monkeypatch):
    enviados = []

    async def registrar(*a):
        return True

    async def historial(tel):
        return [{"role": "user", "content": "hola"}]

    async def generar(h):
        return "Hola, soy Sofía"

    async def guardar(*a):
        pass

    async def enviar(destino, texto):
        enviados.append((destino, texto))

    monkeypatch.setattr(main.store, "registrar_entrante", registrar)
    monkeypatch.setattr(main.store, "obtener_historial", historial)
    monkeypatch.setattr(main.store, "guardar_respuesta", guardar)
    monkeypatch.setattr(main.llm, "generar_respuesta", generar)
    monkeypatch.setattr(main.whatsapp, "enviar_texto", enviar)
    await main.procesar_mensaje("5214931234567", "hola", "wamid.2")
    assert enviados == [("524931234567", "Hola, soy Sofía")]


@pytest.fixture
def anyio_backend():
    return "asyncio"
