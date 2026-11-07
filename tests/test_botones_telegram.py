"""Prueba en vivo del 6 de noviembre: Eduardo no tenía forma directa de aprobar un comprobante y Sofía dio la
sesión por confirmada sin evento en el calendario."""

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import pytest

from app import calendario, comandos, config, guardrails, llm, main, scheduling, telegram
from tests.falsos import _alertas, _enviados

TEL = "5214931234567"


def _boton(data, chat=None):
    mensaje = {"chat": {"id": chat or config.TELEGRAM_CHAT_ID}}
    return {"callback_query": {"id": "1", "data": data, "message": mensaje}}


@pytest.fixture
def tocar(entorno):
    entorno["respx"].post(url__regex=r".*/answerCallbackQuery").mock(return_value=httpx.Response(200, json={}))
    return lambda data, chat=None: asyncio.run(comandos.manejar(_boton(data, chat)))


def test_el_comprobante_trae_las_cuatro_opciones():
    filas = comandos.botones_comprobante(TEL)["inline_keyboard"]

    assert [f[0]["callback_data"].split(":")[0] for f in filas] == [
        "reservar",
        "no_comprobante",
        "mensaje",
        "reanudar",
    ]


def test_no_es_comprobante_avisa_guarda_y_devuelve_a_sofia(entorno, tocar):
    tocar(f"no_comprobante:{TEL}")

    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_NO_COMPROBANTE]
    assert any("✅ Enviado" in a and "vuelve a atender" in a for a in _alertas(entorno["telegram"]))


def test_mensaje_personalizado_pide_contestar_y_se_envia_a_la_persona(entorno, tocar):
    tocar(f"mensaje:{TEL}")
    aviso = _alertas(entorno["telegram"])[-1]

    update = {
        "message": {
            "chat": {"id": config.TELEGRAM_CHAT_ID},
            "text": "Tu pago no aparece aún",
            "reply_to_message": {"text": aviso},
        }
    }
    asyncio.run(comandos.manejar(update))

    assert _enviados(entorno["meta"]) == ["Tu pago no aparece aún"]


def test_reservar_muestra_solo_horas_libres(entorno, tocar, monkeypatch):
    ahora = datetime.now(config.ZONA_MX)
    manana = (ahora + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)

    async def ocupados(desde, usar_cache=True):
        return [(manana, manana + timedelta(hours=1))]

    monkeypatch.setattr(calendario, "ocupados", ocupados)

    tocar(f"reservar:{TEL}")

    cuerpo = json.loads(entorno["telegram"].calls[-1].request.content)
    datos = [b["callback_data"] for fila in cuerpo["reply_markup"]["inline_keyboard"] for b in fila]
    assert datos and f"hora:{TEL}:{manana:%Y%m%d%H}" not in datos


def test_elegir_hora_reserva_y_devuelve_a_sofia(entorno, tocar, monkeypatch):
    elegidos = []

    async def reservar(telefono, inicio):
        elegidos.append((telefono, inicio.hour))
        return True

    monkeypatch.setattr(comandos, "reservar", reservar)

    tocar(f"hora:{TEL}:2030010214")

    assert elegidos == [(TEL, 14)]


@pytest.mark.parametrize("accion", ["reanudar", "no_comprobante", "reservar", "hora"])
def test_un_boton_no_quita_el_bloqueo_por_crisis(entorno, tocar, accion):
    entorno["store"].pausadas[TEL] = "crisis"

    tocar(f"{accion}:{TEL}:2030010214")

    assert TEL in entorno["store"].pausadas
    assert _enviados(entorno["meta"]) == []


def test_un_boton_de_otro_chat_se_ignora(entorno, tocar):
    entorno["store"].pausadas[TEL] = "crisis"

    tocar(f"reanudar:{TEL}", chat="otro-chat")

    assert entorno["telegram"].call_count == 0


def test_horas_libres_no_ofrece_el_pasado():
    ahora = datetime(2030, 1, 2, 15, 30, tzinfo=config.ZONA_MX)

    inicios = scheduling.horas_libres(ahora)

    assert inicios[0].hour == 16 and len(inicios) == 12


def test_sin_sesion_en_el_calendario_el_modelo_recibe_la_nota(entorno):
    contexto = asyncio.run(main.contexto_de(TEL, {"atendida_por_eduardo": False}))

    assert llm.NOTA_SIN_RESERVA in contexto


def test_el_primer_mensaje_aclara_que_es_coaching_y_no_terapia():
    assert "coaching de vida" in guardrails.AVISO_PRIVACIDAD
    assert "No sustituye la terapia" in guardrails.AVISO_PRIVACIDAD


def test_preparar_bot_publica_el_menu_y_pide_los_botones(entorno):
    r = entorno["respx"]
    menu = r.post(url__regex=r".*/setMyCommands").mock(return_value=httpx.Response(200, json={}))
    info = {"result": {"url": "https://x/telegram/webhook", "allowed_updates": ["message"]}}
    r.post(url__regex=r".*/getWebhookInfo").mock(return_value=httpx.Response(200, json=info))
    hook = r.post(url__regex=r".*/setWebhook").mock(return_value=httpx.Response(200, json={}))

    asyncio.run(telegram.preparar_bot())

    assert menu.called
    assert "callback_query" in json.loads(hook.calls[0].request.content)["allowed_updates"]
