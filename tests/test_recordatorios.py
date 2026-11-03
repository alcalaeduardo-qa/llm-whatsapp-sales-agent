"""Recordatorio por WhatsApp a la persona antes de su sesión."""

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app import calendario, config, guardrails, main, recordatorios, store
from tests.falsos import _alertas, _enviados
from tests.test_agenda import CalendarioFalso, evento

MX = config.ZONA_MX
TEL = "5214931234567"
AHORA = datetime(2026, 11, 2, 13, 52, tzinfo=MX)


def sesion(inicio, telefono=TEL, uid="uid-1"):
    e = evento(inicio, inicio + timedelta(hours=1))
    e.add("summary", f"Sesión +{telefono}")
    e.add("uid", uid)
    return e


@pytest.fixture
def cal(monkeypatch):
    falso = CalendarioFalso()
    monkeypatch.setattr(calendario, "_calendario", lambda: falso)
    return falso


# --- Lectura de sesiones del calendario --------------------------------------------------
@pytest.mark.anyio
async def test_solo_cuenta_sesiones_creadas_por_reservar(cal):
    personal = evento(AHORA + timedelta(minutes=5), AHORA + timedelta(hours=1))
    personal.add("summary", "Dentista")
    cal.eventos = [sesion(AHORA + timedelta(minutes=8)), personal]
    assert await calendario.sesiones_entre(AHORA, AHORA + timedelta(minutes=10)) == [
        ("uid-1", AHORA + timedelta(minutes=8), TEL)
    ]


# --- Envío -----------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_manda_recordatorio_10_minutos_antes(entorno, cal):
    cal.eventos = [sesion(datetime(2026, 11, 2, 14, tzinfo=MX))]
    assert await recordatorios.revisar(AHORA) == 1

    (enviado,) = _enviados(entorno["meta"])
    assert enviado == guardrails.RESPUESTA_RECORDATORIO.format(minutos=10, hora="2 PM")
    assert json.loads(entorno["meta"].calls[0].request.content)["to"] == "524931234567"
    assert entorno["store"].respuestas_a(TEL) == [enviado]  # queda en el historial


@pytest.mark.anyio
async def test_no_se_repite_en_la_siguiente_revision(entorno, cal):
    cal.eventos = [sesion(datetime(2026, 11, 2, 14, tzinfo=MX))]
    await recordatorios.revisar(AHORA)
    assert await recordatorios.revisar(AHORA + timedelta(minutes=1)) == 0
    assert entorno["meta"].call_count == 1


@pytest.mark.anyio
async def test_sesion_ya_empezada_no_se_recuerda(entorno, cal):
    cal.eventos = [sesion(AHORA - timedelta(minutes=1))]
    assert await recordatorios.revisar(AHORA) == 0
    assert entorno["meta"].call_count == 0


@pytest.mark.anyio
async def test_varias_sesiones_cada_una_con_su_recordatorio(entorno, cal):
    cal.eventos = [
        sesion(AHORA + timedelta(minutes=5), uid="a"),
        sesion(AHORA + timedelta(minutes=8), telefono="573001234567", uid="b"),
    ]
    assert await recordatorios.revisar(AHORA) == 2
    assert [json.loads(c.request.content)["to"] for c in entorno["meta"].calls] == ["524931234567", "573001234567"]


@pytest.mark.anyio
async def test_sin_registro_en_la_base_no_se_envia(entorno, cal, monkeypatch, caplog):
    async def falla(*_):
        raise RuntimeError("Supabase caído")

    monkeypatch.setattr(store, "marcar_recordatorio", falla)
    cal.eventos = [sesion(AHORA + timedelta(minutes=8))]
    assert await recordatorios.revisar(AHORA) == 0
    assert entorno["meta"].call_count == 0
    assert "no se envía" in caplog.text


# --- Fuera de la ventana de 24 h ---------------------------------------------------------------
@pytest.mark.anyio
async def test_fuera_de_ventana_usa_la_plantilla(entorno, monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_PLANTILLA_RECORDATORIO", "recordatorio_sesion")
    entorno["meta"].mock(
        side_effect=[
            httpx.Response(400, json={"error": {"code": 131047}}),
            httpx.Response(200, json={"messages": [{"id": "wamid.T"}]}),
        ]
    )
    await recordatorios.enviar(TEL, datetime(2026, 11, 2, 15, tzinfo=MX))

    plantilla = json.loads(entorno["meta"].calls[1].request.content)
    assert plantilla["type"] == "template"
    assert plantilla["template"]["name"] == "recordatorio_sesion"
    assert plantilla["template"]["language"] == {"code": "es_MX"}
    assert plantilla["template"]["components"][0]["parameters"] == [{"type": "text", "text": "3 PM"}]
    assert entorno["telegram"].call_count == 0


@pytest.mark.anyio
async def test_sin_plantilla_avisa_a_eduardo(entorno, monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_PLANTILLA_RECORDATORIO", "")
    entorno["meta"].mock(return_value=httpx.Response(400, json={"error": {"code": 131047}}))
    await recordatorios.enviar(TEL, datetime(2026, 11, 2, 15, tzinfo=MX))
    (alerta,) = _alertas(entorno["telegram"])
    assert alerta.startswith(f"⏰ No pude mandarle el recordatorio a +{TEL}") and "Meta respondió 400" in alerta
    assert entorno["store"].respuestas_a(TEL) == []


@pytest.mark.anyio
async def test_plantilla_rechazada_tambien_avisa(entorno, monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_PLANTILLA_RECORDATORIO", "recordatorio_sesion")
    entorno["meta"].mock(
        side_effect=[httpx.Response(400, json={}), httpx.Response(404, json={"error": {"code": 132001}})]
    )
    await recordatorios.enviar(TEL, datetime(2026, 11, 2, 15, tzinfo=MX))
    assert "Meta respondió 404" in _alertas(entorno["telegram"])[0]


def test_texto_del_recordatorio_cumple_las_reglas():
    assert guardrails.validar_salida(guardrails.RESPUESTA_RECORDATORIO.format(minutos=10, hora="2 PM")) == []


# --- Bucle y arranque del servidor --------------------------------------------------------------
@pytest.mark.anyio
async def test_bucle_sigue_aunque_una_revision_falle(monkeypatch, caplog):
    vueltas = []

    async def revisar():
        vueltas.append(1)
        raise RuntimeError("iCloud no responde")

    async def dormir(_segundos):
        if len(vueltas) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(recordatorios, "revisar", revisar)
    monkeypatch.setattr(recordatorios.asyncio, "sleep", dormir)
    with pytest.raises(asyncio.CancelledError):
        await recordatorios.bucle()
    assert len(vueltas) == 2
    assert "Falló la revisión de recordatorios" in caplog.text


@pytest.mark.parametrize("configurado", [True, False])
def test_el_servidor_arranca_el_bucle_solo_con_icloud(monkeypatch, configurado):
    arrancado = []

    async def bucle():
        arrancado.append(True)
        await asyncio.Event().wait()

    monkeypatch.setattr(recordatorios, "bucle", bucle)
    monkeypatch.setattr(calendario, "configurado", lambda: configurado)
    with TestClient(main.app) as cliente:
        assert cliente.get("/health").json() == {"status": "ok"}
    assert arrancado == ([True] if configurado else [])
