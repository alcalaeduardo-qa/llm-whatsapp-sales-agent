"""Dobles de prueba compartidos: store en memoria, Claude falso (httpx2) y el fixture `entorno`
que mockea Meta y Telegram con respx."""

import json

import httpx
import httpx2
import pytest
import respx
from anthropic import AsyncAnthropic
from fastapi.testclient import TestClient

from app import llm, main, store

URL_META = "https://graph.facebook.com/v26.0/111/messages"
URL_TELEGRAM = "https://api.telegram.org/botbot-token/sendMessage"
SALUDO = "Hola, soy Sofía: ¿qué te trae por aquí?"


class StoreFalso:
    """Mismo contrato que app.store, en memoria."""

    def __init__(self):
        self.filas: list[dict] = []
        self.ids: set[str] = set()
        self.pausadas: dict[str, str] = {}
        self.humano: dict[str, str] = {}
        self.motivos: dict[str, str] = {}  # como motivo_humano: se conserva tras /reanudar
        self.recordatorios: set[str] = set()
        self.ventana: bool | None = True  # ¿la persona escribió en las últimas 24 h?
        self.metricas: list[dict] = []
        self.aviso_ya_enviado = True  # los tests del aviso lo ponen en False
        self.avisados: list[str] = []

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

    async def obtener_estado(self, telefono):
        return {
            "venta_pausada": telefono in self.pausadas,
            "humano_activo": telefono in self.humano,
            "atendida_por_eduardo": telefono in self.motivos or telefono in self.humano,
            "aviso_privacidad": self.aviso_ya_enviado or telefono in self.avisados,
        }

    async def marcar_aviso_privacidad(self, telefono):
        self.avisados.append(telefono)
        return True

    async def guardar_metrica(self, fila):
        self.metricas.append(fila)

    async def pausar_venta(self, telefono, motivo):
        self.pausadas[telefono] = motivo
        return True

    async def activar_humano(self, telefono, motivo):
        self.humano.setdefault(telefono, motivo)
        self.motivos[telefono] = motivo
        return True

    async def reanudar(self, telefono):
        self.pausadas.pop(telefono, None)
        self.humano.pop(telefono, None)
        return True

    async def en_ventana_de_whatsapp(self, telefono):
        return self.ventana

    async def borrar_datos(self, telefono):
        propios = [f for f in self.filas if f["telefono"] == telefono]
        self.filas = [f for f in self.filas if f["telefono"] != telefono]
        for estado in (self.pausadas, self.humano, self.motivos):
            estado.pop(telefono, None)
        self.avisados = [t for t in self.avisados if t != telefono]
        return len(propios)

    async def marcar_recordatorio(self, evento_uid, telefono, inicio):
        if evento_uid in self.recordatorios:
            return False
        self.recordatorios.add(evento_uid)
        return True

    def respuestas_a(self, telefono):
        return [f["contenido"] for f in self.filas if f["telefono"] == telefono and f["rol"] == "assistant"]


class ClaudeFalso:
    """Responde como la API de Messages. Separa las llamadas del clasificador de crisis
    de las de generación; `respuestas` se consume en orden y repite la última."""

    def __init__(self):
        self.peticiones: list[dict] = []
        self.clasificaciones: list[dict] = []
        self.respuestas: list[list[dict]] = [[{"type": "text", "text": SALUDO}]]
        self.veredicto = "NORMAL"
        self.clasificador_falla = False
        self.clasificador_stop = "end_turn"
        self.stop_reason = "end_turn"

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        cuerpo = json.loads(request.content)
        if cuerpo["system"] == llm.PROMPT_CLASIFICADOR:
            self.clasificaciones.append(cuerpo)
            if self.clasificador_falla:
                return httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "x"}})
            return self._mensaje([{"type": "text", "text": self.veredicto}], self.clasificador_stop)
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
    for nombre in [n for n in dir(StoreFalso) if not n.startswith("_") and n != "respuestas_a"]:
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
        yield {
            "store": falso,
            "claude": claude,
            "meta": meta,
            "telegram": tg,
            "respx": mock,
            "http": TestClient(main.app),
        }


def _enviados(ruta):
    return [json.loads(c.request.content)["text"]["body"] for c in ruta.calls]


def _alertas(ruta):
    return [json.loads(c.request.content)["text"] for c in ruta.calls]
