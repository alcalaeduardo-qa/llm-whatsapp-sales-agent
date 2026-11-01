"""Webhook de punta a punta: Meta mockeado con respx, Claude con un transporte falso
de httpx2 (el SDK de Anthropic no usa httpx) y un store en memoria."""

import json
import logging

import httpx
import httpx2
import pytest
import respx
from anthropic import AsyncAnthropic
from fastapi.testclient import TestClient

from app import llm, main, store
from tests.conftest import payload, post_firmado

URL_META = "https://graph.facebook.com/v26.0/111/messages"


class StoreFalso:
    """Mismo contrato que app.store, en memoria."""

    def __init__(self):
        self.filas: list[dict] = []
        self.ids: set[str] = set()

    async def registrar_entrante(self, telefono, contenido, wa_message_id):
        if wa_message_id in self.ids:
            return False
        self.ids.add(wa_message_id)
        self.filas.append({"telefono": telefono, "rol": "user", "contenido": contenido})
        return True

    async def guardar_respuesta(self, telefono, contenido):
        self.filas.append({"telefono": telefono, "rol": "assistant", "contenido": contenido})

    async def obtener_historial(self, telefono, limite=20):
        propias = [f for f in self.filas if f["telefono"] == telefono][-limite:]
        return store.preparar_historial(propias)


class ClaudeFalso:
    """Responde como la API de Messages y guarda cada petición recibida."""

    def __init__(self):
        self.peticiones: list[dict] = []
        self.contenido = [{"type": "text", "text": "Hola, soy Sofía: ¿qué te trae por aquí?"}]
        self.stop_reason = "end_turn"

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.peticiones.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": self.contenido,
                "stop_reason": self.stop_reason,
                "stop_sequence": None,
                "usage": {"input_tokens": 120, "output_tokens": 15},
            },
        )


@pytest.fixture
def entorno(monkeypatch):
    falso = StoreFalso()
    for nombre in ("registrar_entrante", "guardar_respuesta", "obtener_historial"):
        monkeypatch.setattr(store, nombre, getattr(falso, nombre))

    claude = ClaudeFalso()
    cliente_claude = AsyncAnthropic(
        api_key="sk-test",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(claude)),
    )
    monkeypatch.setattr(llm, "_cliente", lambda: cliente_claude)

    with respx.mock(assert_all_called=False) as meta:
        ruta = meta.post(URL_META).mock(return_value=httpx.Response(200, json={"messages": [{"id": "wamid.OUT"}]}))
        yield {"store": falso, "claude": claude, "meta": ruta, "http": TestClient(main.app)}


def _enviados(ruta):
    return [json.loads(c.request.content) for c in ruta.calls]


def test_texto_recibe_respuesta_de_sofia(entorno):
    r = post_firmado(entorno["http"], payload(texto="Me siento estancada"))
    assert r.json() == {"status": "recibido"}

    (peticion,) = entorno["claude"].peticiones
    assert peticion["messages"] == [{"role": "user", "content": "Me siento estancada"}]
    assert "Sofía" in peticion["system"] and "{{" not in peticion["system"]

    (enviado,) = _enviados(entorno["meta"])
    assert enviado["to"] == "524931234567"  # número MX normalizado
    assert enviado["text"]["body"] == "Hola, soy Sofía, ¿qué te trae por aquí?"  # limpiar_texto aplicado
    assert entorno["meta"].calls[0].request.headers["Authorization"] == "Bearer token-meta"

    roles = [f["rol"] for f in entorno["store"].filas]
    assert roles == ["user", "assistant"]


def test_duplicado_de_meta_no_responde_dos_veces(entorno):
    post_firmado(entorno["http"], payload(wa_id="wamid.DUP"))
    post_firmado(entorno["http"], payload(wa_id="wamid.DUP"))
    assert len(entorno["claude"].peticiones) == 1
    assert entorno["meta"].call_count == 1


def test_segundo_mensaje_lleva_historial(entorno):
    post_firmado(entorno["http"], payload(texto="Hola", wa_id="wamid.1"))
    post_firmado(entorno["http"], payload(texto="Tengo ansiedad", wa_id="wamid.2"))
    mensajes = entorno["claude"].peticiones[1]["messages"]
    assert [m["role"] for m in mensajes] == ["user", "assistant", "user"]
    assert mensajes[-1]["content"] == "Tengo ansiedad"


def test_evento_de_estado_no_llama_a_nadie(entorno):
    datos = {"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]}
    assert post_firmado(entorno["http"], datos).json() == {"status": "ignorado"}
    assert entorno["claude"].peticiones == [] and entorno["meta"].call_count == 0


@pytest.mark.parametrize("tipo", ["audio", "image", "sticker"])
def test_no_texto_no_llama_a_nadie(entorno, tipo):
    assert post_firmado(entorno["http"], payload(tipo=tipo)).json() == {"status": "ignorado"}
    assert entorno["claude"].peticiones == [] and entorno["meta"].call_count == 0
    assert entorno["store"].filas == []


def test_firma_invalida_no_llama_a_nadie(entorno):
    r = post_firmado(entorno["http"], payload(), firma="sha256=" + "0" * 64)
    assert r.status_code == 401
    assert entorno["claude"].peticiones == [] and entorno["meta"].call_count == 0
    assert entorno["store"].filas == []


def test_claude_sin_texto_no_envia_nada(entorno):
    entorno["claude"].contenido = []
    post_firmado(entorno["http"], payload())
    assert entorno["meta"].call_count == 0
    assert [f["rol"] for f in entorno["store"].filas] == ["user"]


def test_respuesta_cortada_por_tokens_se_envia_y_avisa(entorno, caplog):
    entorno["claude"].stop_reason = "max_tokens"
    post_firmado(entorno["http"], payload())
    assert entorno["meta"].call_count == 1
    assert "cortada" in caplog.text


def test_error_de_meta_no_tumba_el_webhook(entorno, caplog):
    entorno["meta"].mock(return_value=httpx.Response(401, json={"error": {"message": "token vencido"}}))
    r = post_firmado(entorno["http"], payload())
    assert r.status_code == 200
    assert "Meta respondió 401" in caplog.text
    assert "Error procesando mensaje wamid.ABC" in caplog.text


def test_logs_no_exponen_el_texto_del_usuario(entorno, caplog):
    caplog.set_level(logging.INFO)
    post_firmado(entorno["http"], payload(texto="Mi secreto muy personal"))
    assert "Mi secreto muy personal" not in caplog.text
    assert "chars #" in caplog.text
