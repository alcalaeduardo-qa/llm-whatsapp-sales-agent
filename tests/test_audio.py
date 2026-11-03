"""Día 6: notas de voz transcritas con Groq."""

import httpx
import pytest

from app import config, guardrails, transcripcion
from tests.conftest import post_firmado
from tests.falsos import _alertas, _enviados

TEL = "5214931234567"
URL_GRAPH = "https://graph.facebook.com/v26.0"
# Fixture de audio: cabecera de un archivo Ogg/Opus (Groq real no se llama en los tests)
AUDIO_OGG = b"OggS\x00\x02" + b"\x00" * 64


def _nota_de_voz(wa_id="wamid.AUDIO"):
    msg = {"from": TEL, "id": wa_id, "type": "audio", "audio": {"id": "AUD1", "mime_type": "audio/ogg; codecs=opus"}}
    return {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


@pytest.fixture
def groq(entorno, monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_prueba")
    mock = entorno["respx"]
    mock.get(f"{URL_GRAPH}/AUD1").mock(
        return_value=httpx.Response(
            200, json={"url": "https://lookaside.fbsbx.com/a", "mime_type": "audio/ogg; codecs=opus"}
        )
    )
    mock.get("https://lookaside.fbsbx.com/a").mock(return_value=httpx.Response(200, content=AUDIO_OGG))
    return mock.post(transcripcion.URL_GROQ).mock(
        return_value=httpx.Response(200, json={"text": " Hola Sofía, tengo ansiedad desde hace meses. "})
    )


# --- Flujo completo -----------------------------------------------------------------------------
def test_nota_de_voz_se_transcribe_y_se_responde(entorno, groq):
    assert post_firmado(entorno["http"], _nota_de_voz()).json() == {"status": "recibido"}

    (llamada,) = groq.calls
    assert llamada.request.headers["Authorization"] == "Bearer gsk_prueba"
    cuerpo = llamada.request.content
    assert AUDIO_OGG in cuerpo and b'filename="nota.ogg"' in cuerpo
    assert b"whisper-large-v3-turbo" in cuerpo and b'name="language"\r\n\r\nes' in cuerpo

    (peticion,) = entorno["claude"].peticiones
    assert peticion["messages"][-1]["content"] == "[nota de voz] Hola Sofía, tengo ansiedad desde hace meses."
    assert "NOTAS DE VOZ" in peticion["system"]  # Claude sabe que es una transcripción
    assert len(_enviados(entorno["meta"])) == 1


def test_nota_de_voz_duplicada_no_responde_dos_veces(entorno, groq):
    post_firmado(entorno["http"], _nota_de_voz())
    post_firmado(entorno["http"], _nota_de_voz())
    assert len(entorno["claude"].peticiones) == 1 and entorno["meta"].call_count == 1


def test_crisis_en_nota_de_voz_activa_el_protocolo(entorno, groq):
    groq.mock(return_value=httpx.Response(200, json={"text": "ya no quiero vivir"}))
    post_firmado(entorno["http"], _nota_de_voz())
    assert entorno["claude"].peticiones == []
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_CRISIS]
    assert entorno["store"].pausadas == {TEL: "crisis"}


def test_nota_de_voz_en_modo_humano_llega_transcrita_a_eduardo(entorno, groq):
    entorno["store"].humano[TEL] = "eduardo_responde"
    post_firmado(entorno["http"], _nota_de_voz())
    assert entorno["meta"].call_count == 0
    assert "[nota de voz] Hola Sofía, tengo ansiedad" in _alertas(entorno["telegram"])[0]


def test_sin_notas_de_voz_no_hay_nota_para_claude(entorno, groq):
    from tests.conftest import payload

    post_firmado(entorno["http"], payload(texto="Hola"))
    assert "NOTAS DE VOZ" not in entorno["claude"].peticiones[0]["system"]


# --- Cuando no se puede transcribir -------------------------------------------------------------
@pytest.mark.parametrize(
    "respuesta",
    [
        httpx.Response(500, json={"error": {"message": "caído"}}),
        httpx.Response(200, json={"text": "   "}),
        httpx.Response(200, content=b"no es json"),
    ],
)
def test_transcripcion_fallida_pide_que_lo_escriba(entorno, groq, respuesta):
    groq.mock(return_value=respuesta)
    post_firmado(entorno["http"], _nota_de_voz())
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_AUDIO_FALLIDO]
    assert entorno["claude"].peticiones == []
    assert entorno["store"].filas[0]["contenido"] == "[nota de voz sin transcribir]"


def test_sin_clave_de_groq_pide_que_lo_escriba(entorno, groq, monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    post_firmado(entorno["http"], _nota_de_voz())
    assert groq.call_count == 0
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_AUDIO_FALLIDO]


def test_descarga_fallida_pide_que_lo_escriba(entorno, groq):
    entorno["respx"].get(f"{URL_GRAPH}/AUD1").mock(return_value=httpx.Response(404))
    post_firmado(entorno["http"], _nota_de_voz())
    assert groq.call_count == 0
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_AUDIO_FALLIDO]


def test_fallida_y_duplicada_no_repite_el_aviso(entorno, groq):
    groq.mock(return_value=httpx.Response(500))
    post_firmado(entorno["http"], _nota_de_voz())
    post_firmado(entorno["http"], _nota_de_voz())
    assert entorno["meta"].call_count == 1


def test_fallida_en_modo_humano_avisa_a_eduardo(entorno, groq):
    groq.mock(return_value=httpx.Response(500))
    entorno["store"].humano[TEL] = "comprobante"
    post_firmado(entorno["http"], _nota_de_voz())
    assert entorno["meta"].call_count == 0
    assert "nota de voz que no pude transcribir" in _alertas(entorno["telegram"])[0]


def test_error_al_avisar_no_tumba_el_webhook(entorno, groq, caplog):
    groq.mock(return_value=httpx.Response(500))
    entorno["meta"].mock(return_value=httpx.Response(500))
    assert post_firmado(entorno["http"], _nota_de_voz()).status_code == 200
    assert "Error avisando de la nota de voz sin transcribir" in caplog.text


def test_respuesta_de_audio_fallido_cumple_las_reglas():
    assert guardrails.validar_salida(guardrails.RESPUESTA_AUDIO_FALLIDO) == []


def test_sofia_no_escribe_la_marca_de_nota_de_voz():
    assert "marca interna de nota de voz" in guardrails.validar_salida("[nota de voz] Claro, te entiendo")


# --- Unidad -------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "mime, extension",
    [
        ("audio/ogg; codecs=opus", "ogg"),
        ("audio/mpeg", "mp3"),
        ("audio/mp4", "m4a"),
        ("audio/x-m4a", "m4a"),
        ("audio/wav", "wav"),
        ("", "ogg"),
    ],
)
def test_extension_por_tipo_de_audio(mime, extension):
    assert transcripcion._extension(mime) == extension


@pytest.mark.anyio
async def test_audio_vacio_o_enorme(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_prueba")
    with pytest.raises(transcripcion.TranscripcionFallida):
        await transcripcion.transcribir(b"", "audio/ogg")
    with pytest.raises(transcripcion.TranscripcionFallida):
        await transcripcion.transcribir(b"x" * (transcripcion.TAMANO_MAXIMO + 1), "audio/ogg")


@pytest.mark.anyio
async def test_groq_rechazado_queda_en_el_log(groq, caplog):
    groq.mock(return_value=httpx.Response(400, json={"error": {"message": "file must be one of..."}}))
    with pytest.raises(transcripcion.TranscripcionFallida):
        await transcripcion.transcribir(AUDIO_OGG, "audio/ogg")
    assert "Groq respondió 400" in caplog.text and "file must be one of" in caplog.text
    assert "gsk_prueba" not in caplog.text  # la clave no se filtra


def test_parametros_enviados_a_groq(entorno, groq):
    post_firmado(entorno["http"], _nota_de_voz())
    cuerpo = groq.calls[0].request.content.decode("latin-1")
    for campo, valor in [("model", "whisper-large-v3-turbo"), ("response_format", "json"), ("temperature", "0")]:
        assert f'name="{campo}"\r\n\r\n{valor}' in cuerpo
