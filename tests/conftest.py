import hashlib
import hmac
import json

import pytest

from app import config
from tests.falsos import entorno  # noqa: F401  (fixture compartido)

SECRETO = "secreto-de-prueba"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def config_de_prueba(monkeypatch):
    """Valores fijos para que los tests no dependan del .env local."""
    monkeypatch.setattr(config, "APP_SECRET", SECRETO)
    monkeypatch.setattr(config, "VERIFY_TOKEN", "tok")
    monkeypatch.setattr(config, "WHATSAPP_TOKEN", "token-meta")
    monkeypatch.setattr(config, "PHONE_NUMBER_ID", "111")
    monkeypatch.setattr(config, "GRAPH_API_VERSION", "v26.0")
    monkeypatch.setattr(config, "LOG_CONTENIDO", False)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "999")
    monkeypatch.setattr(config, "CRISIS_LLM", True)
    monkeypatch.setattr(config, "MAX_CARACTERES", 900)
    monkeypatch.setattr(config, "MAX_PARRAFOS", 4)


def firmar(cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).hexdigest()


def payload(tipo="text", texto="Hola", wa_id="wamid.ABC", remitente="5214931234567"):
    msg = {"from": remitente, "id": wa_id, "type": tipo}
    if tipo == "text":
        msg["text"] = {"body": texto}
    return {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


def post_firmado(cliente, datos, firma=None):
    cuerpo = datos if isinstance(datos, bytes) else json.dumps(datos).encode()
    return cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Hub-Signature-256": firma or firmar(cuerpo), "Content-Type": "application/json"},
    )
