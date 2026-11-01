"""Webhook de punta a punta: Meta y Telegram mockeados con respx, Claude con un transporte
falso de httpx2 (el SDK de Anthropic no usa httpx) y un store en memoria."""

import json
import logging

import httpx
import httpx2
import pytest
import respx
from anthropic import AsyncAnthropic
from fastapi.testclient import TestClient

from app import guardrails, llm, main, store
from tests.conftest import payload, post_firmado

URL_META = "https://graph.facebook.com/v26.0/111/messages"
URL_TELEGRAM = "https://api.telegram.org/botbot-token/sendMessage"
SALUDO = "Hola, soy Sofía: ¿qué te trae por aquí?"


class StoreFalso:
    """Mismo contrato que app.store, en memoria."""

    def __init__(self):
        self.filas: list[dict] = []
        self.ids: set[str] = set()
        self.pausadas: dict[str, str] = {}

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

    async def venta_pausada(self, telefono):
        return telefono in self.pausadas

    async def pausar_venta(self, telefono, motivo):
        self.pausadas[telefono] = motivo


class ClaudeFalso:
    """Responde como la API de Messages. Separa las llamadas del clasificador de crisis
    de las de generación; `respuestas` se consume en orden y repite la última."""

    def __init__(self):
        self.peticiones: list[dict] = []
        self.clasificaciones: list[dict] = []
        self.respuestas: list[list[dict]] = [[{"type": "text", "text": SALUDO}]]
        self.veredicto = "NO"
        self.clasificador_falla = False
        self.stop_reason = "end_turn"

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        cuerpo = json.loads(request.content)
        if cuerpo["system"] == llm.PROMPT_CLASIFICADOR:
            self.clasificaciones.append(cuerpo)
            if self.clasificador_falla:
                return httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "x"}})
            return self._mensaje([{"type": "text", "text": self.veredicto}], "end_turn")
        self.peticiones.append(cuerpo)
        indice = min(len(self.peticiones), len(self.respuestas)) - 1
        return self._mensaje(self.respuestas[indice], self.stop_reason)

    @staticmethod
    def _mensaje(contenido, stop_reason):
        return httpx2.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": contenido,
                "stop_reason": stop_reason,
                "stop_sequence": None,
                "usage": {"input_tokens": 120, "output_tokens": 15},
            },
        )


def _texto(t):
    return [{"type": "text", "text": t}]


@pytest.fixture
def entorno(monkeypatch):
    falso = StoreFalso()
    for nombre in ("registrar_entrante", "guardar_respuesta", "obtener_historial", "venta_pausada", "pausar_venta"):
        monkeypatch.setattr(store, nombre, getattr(falso, nombre))

    claude = ClaudeFalso()
    cliente_claude = AsyncAnthropic(
        api_key="sk-test",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(claude)),
    )
    monkeypatch.setattr(llm, "_cliente", lambda: cliente_claude)

    with respx.mock(assert_all_called=False) as mock:
        meta = mock.post(URL_META).mock(return_value=httpx.Response(200, json={"messages": [{"id": "wamid.OUT"}]}))
        tg = mock.post(URL_TELEGRAM).mock(return_value=httpx.Response(200, json={"ok": True}))
        yield {"store": falso, "claude": claude, "meta": meta, "telegram": tg, "http": TestClient(main.app)}


def _enviados(ruta):
    return [json.loads(c.request.content)["text"]["body"] for c in ruta.calls]


def _alertas(ruta):
    return [json.loads(c.request.content)["text"] for c in ruta.calls]


# --- Flujo normal ------------------------------------------------------------
def test_texto_recibe_respuesta_de_sofia(entorno):
    r = post_firmado(entorno["http"], payload(texto="Me siento estancada"))
    assert r.json() == {"status": "recibido"}

    (peticion,) = entorno["claude"].peticiones
    assert peticion["messages"] == [{"role": "user", "content": "Me siento estancada"}]
    assert "Sofía" in peticion["system"] and "{{" not in peticion["system"]
    assert len(entorno["claude"].clasificaciones) == 1  # se revisó crisis antes de vender

    (llamada,) = entorno["meta"].calls
    enviado = json.loads(llamada.request.content)
    assert enviado["to"] == "524931234567"  # número MX normalizado
    assert enviado["text"]["body"] == "Hola, soy Sofía, ¿qué te trae por aquí?"  # limpiar_texto aplicado
    assert llamada.request.headers["Authorization"] == "Bearer token-meta"

    assert [f["rol"] for f in entorno["store"].filas] == ["user", "assistant"]
    assert entorno["telegram"].call_count == 0


def test_duplicado_de_meta_no_responde_dos_veces(entorno):
    post_firmado(entorno["http"], payload(wa_id="wamid.DUP"))
    post_firmado(entorno["http"], payload(wa_id="wamid.DUP"))
    assert len(entorno["claude"].peticiones) == 1
    assert len(entorno["claude"].clasificaciones) == 1
    assert entorno["meta"].call_count == 1


def test_segundo_mensaje_lleva_historial(entorno):
    post_firmado(entorno["http"], payload(texto="Hola", wa_id="wamid.1"))
    post_firmado(entorno["http"], payload(texto="Tengo ansiedad", wa_id="wamid.2"))
    mensajes = entorno["claude"].peticiones[1]["messages"]
    assert [m["role"] for m in mensajes] == ["user", "assistant", "user"]
    assert mensajes[-1]["content"] == "Tengo ansiedad"
    # El clasificador ve los últimos mensajes de la persona, no solo el actual
    assert entorno["claude"].clasificaciones[1]["messages"][0]["content"] == "Hola\nTengo ansiedad"


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


# --- Crisis ------------------------------------------------------------------
def test_crisis_explicita_respuesta_fija_pausa_y_alerta(entorno):
    post_firmado(entorno["http"], payload(texto="Ya no quiero vivir, pienso en quitarme la vida"))

    assert entorno["claude"].peticiones == []  # no se vende ni se genera nada
    assert entorno["claude"].clasificaciones == []  # palabras explícitas: no hace falta el modelo
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_CRISIS]
    assert entorno["store"].pausadas == {"5214931234567": "crisis"}
    (alerta,) = _alertas(entorno["telegram"])
    assert "Posible crisis de +5214931234567" in alerta
    assert entorno["store"].filas[-1] == {
        "telefono": "5214931234567",
        "rol": "assistant",
        "contenido": guardrails.RESPUESTA_CRISIS,
    }


def test_crisis_encubierta_la_detecta_el_clasificador(entorno):
    entorno["claude"].veredicto = "SI"
    post_firmado(entorno["http"], payload(texto="Ya les escribí una carta a todos para despedirme"))
    assert entorno["claude"].peticiones == []
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_CRISIS]
    assert entorno["telegram"].call_count == 1


def test_clasificador_caido_con_senal_ambigua_es_crisis(entorno):
    entorno["claude"].clasificador_falla = True
    post_firmado(entorno["http"], payload(texto="Ya no puedo más con todo esto"))
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_CRISIS]


def test_clasificador_caido_sin_senales_sigue_normal(entorno):
    entorno["claude"].clasificador_falla = True
    post_firmado(entorno["http"], payload(texto="¿Cuánto cuesta la sesión?"))
    assert len(entorno["claude"].peticiones) == 1
    assert entorno["store"].pausadas == {}


def test_conversacion_en_pausa_no_vuelve_a_vender(entorno):
    post_firmado(entorno["http"], payload(texto="me quiero morir", wa_id="wamid.1"))
    post_firmado(entorno["http"], payload(texto="¿Entonces cuánto cuesta?", wa_id="wamid.2"))

    assert entorno["claude"].peticiones == []
    assert entorno["claude"].clasificaciones == []
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_CRISIS, guardrails.RESPUESTA_PAUSA]
    assert "conversación en pausa" in _alertas(entorno["telegram"])[1]


def test_crisis_se_atiende_aunque_falle_telegram(entorno):
    entorno["telegram"].mock(return_value=httpx.Response(502))
    post_firmado(entorno["http"], payload(texto="quiero hacerme daño"))
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_CRISIS]


def test_alerta_sale_aunque_falle_whatsapp(entorno):
    entorno["meta"].mock(return_value=httpx.Response(500))
    post_firmado(entorno["http"], payload(texto="quiero hacerme daño"))
    assert entorno["telegram"].call_count == 1


# --- Validador de salida: reintento y respaldo -------------------------------
def test_respuesta_invalida_se_reintenta_con_correccion(entorno):
    corregida = "Eduardo acompaña en sesiones privadas, la inversión es de $300 pesos mexicanos."
    entorno["claude"].respuestas = [_texto("Eduardo es terapeuta y te cobra $250"), _texto(corregida)]
    post_firmado(entorno["http"], payload())

    primera, segunda = entorno["claude"].peticiones
    assert "CORRECCIÓN" not in primera["system"]
    assert "palabra prohibida terapeuta" in segunda["system"]
    assert "precio no autorizado $250" in segunda["system"]
    assert _enviados(entorno["meta"]) == [corregida]
    assert entorno["telegram"].call_count == 0


def test_dos_respuestas_invalidas_usan_respaldo_y_alertan(entorno):
    entorno["claude"].respuestas = [_texto("**Fase 3** la terapia cuesta $100")]
    post_firmado(entorno["http"], payload())

    assert len(entorno["claude"].peticiones) == 2  # un solo reintento
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_RESPALDO]
    (alerta,) = _alertas(entorno["telegram"])
    assert "respaldo" in alerta and "palabra prohibida terapia" in alerta
    assert entorno["store"].filas[-1]["contenido"] == guardrails.RESPUESTA_RESPALDO


def test_claude_sin_texto_termina_en_respaldo(entorno):
    entorno["claude"].respuestas = [[]]
    post_firmado(entorno["http"], payload())
    assert len(entorno["claude"].peticiones) == 2
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_RESPALDO]
