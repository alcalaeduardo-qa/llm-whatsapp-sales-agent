"""Auditoría de seguridad: los logs no guardan datos de las personas y las respuestas del operador no se desvían.

Con LOG_CONTENIDO=false (producción) ningún log debe llevar texto, teléfonos ni identificadores de mensaje: los
`wamid` de Meta llevan el teléfono codificado. Y la respuesta de Eduardo debe ir al número del encabezado de la
alerta, nunca a uno que la persona escribió en su mensaje.
"""

import json
import logging

import httpx
import pytest

from app import comandos, config, telegram, whatsapp
from app.text import huella, normalizar_numero
from tests.conftest import payload, post_firmado
from tests.falsos import _enviados
from tests.test_audio import _nota_de_voz
from tests.test_derivacion import SECRETO_TG, URL_DOCUMENTO, _media, _mockear_descarga, _telegram, _update

TEL = "5214931234567"
MARCA = "ASUNTO-MUY-PERSONAL-4821"
WAMID = "wamid.HBgNNTIxNDQ5MzEyMzQ1NjcVAgASGBQzQUNFOTk"  # el teléfono va codificado dentro del identificador


@pytest.fixture(autouse=True)
def _secreto_telegram(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", SECRETO_TG)


# --- telegram-failure-paths-log-plaintext-alert-content --------------------------------------------
@pytest.mark.anyio
async def test_si_telegram_rechaza_la_alerta_el_log_no_lleva_el_texto(entorno, caplog):
    entorno["telegram"].mock(return_value=httpx.Response(500))

    assert await telegram.alertar(f"💬 +{TEL}\n{MARCA}") is False

    assert MARCA not in caplog.text and TEL not in caplog.text
    assert "No se pudo enviar la alerta a Telegram" in caplog.text


@pytest.mark.anyio
async def test_si_telegram_no_esta_configurado_el_log_no_lleva_el_texto(entorno, monkeypatch, caplog):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")

    await telegram.alertar(f"💬 +{TEL}\n{MARCA}")
    await telegram.enviar_archivo(b"x", "a.jpg", "image/jpeg", f"🧾 +{TEL}\n{MARCA}")

    assert MARCA not in caplog.text and TEL not in caplog.text
    assert "ALERTA sin Telegram configurado" in caplog.text and "ARCHIVO sin Telegram configurado" in caplog.text


@pytest.mark.anyio
async def test_si_telegram_rechaza_el_archivo_el_log_no_lleva_la_leyenda(entorno, caplog):
    entorno["respx"].post(URL_DOCUMENTO).mock(return_value=httpx.Response(500))

    assert await telegram.enviar_archivo(b"x", "a.jpg", "image/jpeg", f"🧾 +{TEL}\n{MARCA}") is False

    assert MARCA not in caplog.text and TEL not in caplog.text


@pytest.mark.anyio
async def test_con_log_de_contenido_activado_el_texto_si_se_registra(entorno, monkeypatch, caplog):
    monkeypatch.setattr(config, "LOG_CONTENIDO", True)  # solo para depurar en local
    entorno["telegram"].mock(return_value=httpx.Response(500))

    await telegram.alertar(MARCA)

    assert MARCA in caplog.text


# --- logs:ungated-person-identifiers ---------------------------------------------------------------
def test_los_logs_no_llevan_el_identificador_del_mensaje(entorno, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    _mockear_descarga(entorno)
    ids = {nombre: f"{WAMID}{nombre}" for nombre in ("texto", "crisis", "imagen", "audio", "sticker")}

    post_firmado(entorno["http"], payload(texto="Hola", wa_id=ids["texto"]))
    post_firmado(entorno["http"], payload(texto="Hola", wa_id=ids["texto"]))  # duplicado
    post_firmado(entorno["http"], payload(texto="me quiero morir", wa_id=ids["crisis"]))
    post_firmado(entorno["http"], _media(wa_id=ids["imagen"]))
    post_firmado(entorno["http"], _nota_de_voz(wa_id=ids["audio"]))
    post_firmado(entorno["http"], payload(tipo="sticker", wa_id=ids["sticker"]))

    for nombre, wamid in ids.items():
        assert wamid not in caplog.text, nombre
    assert "Crisis detectada" in caplog.text and "duplicado" in caplog.text
    assert f"#{huella(ids['crisis'])}" in caplog.text  # sigue habiendo cómo seguir un mensaje en los logs


def test_un_error_al_procesar_tampoco_expone_el_identificador(entorno, caplog):
    entorno["meta"].mock(return_value=httpx.Response(401, json={}))

    post_firmado(entorno["http"], payload(texto="Hola", wa_id=WAMID))

    assert "Error procesando mensaje" in caplog.text and WAMID not in caplog.text


def test_el_aviso_de_envio_fallido_no_lleva_el_telefono(entorno, caplog):
    estado = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [
                                {"status": "failed", "recipient_id": TEL, "errors": [{"code": 131047, "title": "x"}]}
                            ]
                        }
                    }
                ]
            }
        ]
    }

    post_firmado(entorno["http"], estado)

    assert "Meta no entregó un mensaje" in caplog.text and TEL not in caplog.text


def test_el_error_de_meta_deja_el_codigo_pero_no_el_cuerpo(entorno, caplog):
    cuerpo = {"error": {"message": f"Invalid recipient {TEL}", "type": "OAuthException", "code": 131030}}
    entorno["meta"].mock(return_value=httpx.Response(400, json=cuerpo))

    post_firmado(entorno["http"], payload(texto="Hola", wa_id="wamid.ABC"))

    assert "Meta respondió 400" in caplog.text and "131030" in caplog.text
    assert TEL not in caplog.text


def test_un_cuerpo_de_error_que_no_es_json_no_rompe_el_log(entorno, caplog):
    entorno["meta"].mock(return_value=httpx.Response(502, text=f"<html>{TEL}</html>"))

    post_firmado(entorno["http"], payload(texto="Hola", wa_id="wamid.ABC"))

    assert "Meta respondió 502" in caplog.text and TEL not in caplog.text


@pytest.mark.anyio
async def test_enviar_texto_lanza_el_error_de_meta(entorno):
    entorno["meta"].mock(return_value=httpx.Response(400, json={"error": {"code": 1}}))
    with pytest.raises(httpx.HTTPStatusError):
        await whatsapp.enviar_texto(TEL, "hola")


# --- comandos-telefono-citado-por-regex-en-texto-libre ---------------------------------------------
def _destinos(entorno):
    return [json.loads(c.request.content)["to"] for c in entorno["meta"].calls]


def test_la_respuesta_va_al_numero_del_encabezado_aunque_sea_corto(entorno):
    citado = {"text": "💬 +1234567\nUn mensaje con otro número +5210000000001 dentro\n\nContesta a este mensaje"}

    _telegram(entorno, _update("Gracias, te escribo", citado=citado))

    assert _destinos(entorno) == ["1234567"]
    assert list(entorno["store"].humano) == ["1234567"]


def test_el_numero_largo_del_encabezado_sigue_funcionando(entorno):
    citado = {"text": f"💬 +{TEL}\nHola, mi otro número es +5210000000001"}

    _telegram(entorno, _update("Gracias", citado=citado))

    assert _destinos(entorno) == [normalizar_numero(TEL)]


def test_contestar_a_la_ayuda_no_manda_nada_a_un_numero_de_ejemplo(entorno):
    _telegram(entorno, _update("hola", citado={"text": comandos.AYUDA}))

    assert entorno["meta"].call_count == 0
    assert entorno["store"].humano == {}
    assert "contesta (reply) a la alerta" in json.loads(entorno["telegram"].calls[0].request.content)["text"]


def test_ninguna_respuesta_normal_cambia(entorno):
    _telegram(entorno, _update("/responder 5214931234567 hola"))

    assert _enviados(entorno["meta"]) == ["hola"]
