"""Día 4: derivación a Eduardo (tool use), modo humano, comprobantes y comandos de Telegram."""

import json

import httpx
import pytest

from app import comandos, config, guardrails, telegram
from tests.conftest import payload, post_firmado
from tests.falsos import _alertas, _enviados, _texto

TEL = "5214931234567"
URL_GRAPH = "https://graph.facebook.com/v26.0"
URL_DOCUMENTO = "https://api.telegram.org/botbot-token/sendDocument"
SECRETO_TG = "secreto-telegram"


def _derivar(motivo="pide_hablar_con_eduardo", resumen="Quiere hablar con Eduardo sobre su pareja"):
    return {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "derivar_a_eduardo",
        "input": {"motivo": motivo, "resumen": resumen},
    }


def _media(tipo="image", caption=None, wa_id="wamid.IMG"):
    contenido = {"id": "MEDIA1", **({"caption": caption} if caption else {})}
    msg = {"from": TEL, "id": wa_id, "type": tipo, tipo: contenido}
    return {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


def _mockear_descarga(entorno, ok=True):
    mock = entorno["respx"]
    if ok:
        mock.get(f"{URL_GRAPH}/MEDIA1").mock(
            return_value=httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/x", "mime_type": "image/jpeg"})
        )
        mock.get("https://lookaside.fbsbx.com/x").mock(return_value=httpx.Response(200, content=b"\xff\xd8jpeg"))
    else:
        mock.get(f"{URL_GRAPH}/MEDIA1").mock(return_value=httpx.Response(404))
    return mock.post(URL_DOCUMENTO).mock(return_value=httpx.Response(200, json={"ok": True}))


def _update(texto, chat_id="999", citado=None):
    mensaje = {"message_id": 1, "chat": {"id": int(chat_id)}, "text": texto}
    if citado is not None:
        mensaje["reply_to_message"] = citado
    return {"update_id": 1, "message": mensaje}


def _telegram(entorno, update, secreto=SECRETO_TG):
    return entorno["http"].post(
        "/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": secreto} if secreto else {}
    )


@pytest.fixture(autouse=True)
def secreto_telegram(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", SECRETO_TG)


# --- Derivación por tool use ---------------------------------------------------
def test_claude_deriva_avisa_y_se_calla(entorno):
    entorno["claude"].respuestas = [[_texto("Gracias por decirme, Eduardo te escribirá personalmente.")[0], _derivar()]]
    post_firmado(entorno["http"], payload(texto="Quiero hablar con Eduardo directamente", wa_id="w1"))

    assert entorno["claude"].peticiones[0]["tools"][0]["name"] == "derivar_a_eduardo"
    assert _enviados(entorno["meta"]) == ["Gracias por decirme, Eduardo te escribirá personalmente."]
    assert entorno["store"].humano == {TEL: "pide_hablar_con_eduardo"}
    (alerta,) = _alertas(entorno["telegram"])
    assert f"Derivación de +{TEL} (pide_hablar_con_eduardo)" in alerta
    assert "Quiere hablar con Eduardo sobre su pareja" in alerta
    assert "Quiero hablar con Eduardo directamente" in alerta  # Eduardo ve el último mensaje
    assert f"/reanudar {TEL}" in alerta

    # Siguiente mensaje: Sofía no responde, se reenvía a Eduardo
    post_firmado(entorno["http"], payload(texto="¿Me va a escribir hoy?", wa_id="w2"))
    assert len(entorno["claude"].peticiones) == 1 and len(entorno["claude"].clasificaciones) == 1
    assert entorno["meta"].call_count == 1
    assert f"💬 +{TEL}\n¿Me va a escribir hoy?" in _alertas(entorno["telegram"])[1]


def test_derivacion_sin_texto_valido_usa_mensaje_fijo(entorno):
    entorno["claude"].respuestas = [[_derivar("comprobante", "Dice que ya transfirió")]]
    post_firmado(entorno["http"], payload(texto="Ya te pagué"))
    assert len(entorno["claude"].peticiones) == 1  # derivar no reintenta
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_DERIVACION]
    assert entorno["store"].humano == {TEL: "comprobante"}


def test_derivacion_con_texto_invalido_usa_mensaje_fijo(entorno):
    entorno["claude"].respuestas = [[_texto("Claro, tu terapeuta Eduardo te atiende")[0], _derivar()]]
    post_firmado(entorno["http"], payload(texto="Pásame con Eduardo"))
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_DERIVACION]


def test_derivacion_en_el_reintento(entorno):
    entorno["claude"].respuestas = [_texto("Esto es terapia"), [_derivar("fuera_de_fase", "Pregunta por factura")]]
    post_firmado(entorno["http"], payload(texto="¿Me dan factura?"))
    assert len(entorno["claude"].peticiones) == 2
    assert entorno["store"].humano == {TEL: "fuera_de_fase"}


def test_derivacion_alerta_aunque_falle_whatsapp(entorno):
    entorno["claude"].respuestas = [[_derivar()]]
    entorno["meta"].mock(return_value=httpx.Response(500))
    post_firmado(entorno["http"], payload(texto="Quiero hablar con Eduardo"))
    assert entorno["store"].humano == {TEL: "pide_hablar_con_eduardo"}
    assert entorno["telegram"].call_count == 1


def test_modo_humano_con_crisis_explicita_si_responde_linea_de_ayuda(entorno):
    entorno["store"].humano[TEL] = "comprobante"
    post_firmado(entorno["http"], payload(texto="ya no quiero vivir"))
    assert entorno["claude"].peticiones == [] and entorno["claude"].clasificaciones == []
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_CRISIS]
    assert _alertas(entorno["telegram"])[0].startswith("🚨 Posible crisis")


def test_modo_humano_crisis_alerta_aunque_falle_whatsapp(entorno):
    entorno["store"].humano[TEL] = "comprobante"
    entorno["meta"].mock(return_value=httpx.Response(500))
    post_firmado(entorno["http"], payload(texto="me quiero morir"))
    assert entorno["telegram"].call_count == 1


def test_modo_humano_tiene_prioridad_sobre_pausa_por_crisis(entorno):
    entorno["store"].pausadas[TEL] = "crisis"
    entorno["store"].humano[TEL] = "eduardo_responde"
    post_firmado(entorno["http"], payload(texto="Gracias Eduardo"))
    assert entorno["meta"].call_count == 0  # Eduardo ya está atendiendo
    assert "Gracias Eduardo" in _alertas(entorno["telegram"])[0]


# --- Comprobantes (imagen o documento) -------------------------------------------
def test_imagen_se_reenvia_a_eduardo_y_deriva(entorno):
    documento = _mockear_descarga(entorno)
    r = post_firmado(entorno["http"], _media(caption="Listo, ya transferí"))
    assert r.json() == {"status": "recibido"}

    (envio,) = documento.calls
    cuerpo = envio.request.content
    assert b"\xff\xd8jpeg" in cuerpo and f"imagen_{TEL}.jpeg".encode() in cuerpo
    assert "Listo, ya transferí".encode() in cuerpo and f"/reanudar {TEL}".encode() in cuerpo
    assert entorno["claude"].peticiones == []
    assert entorno["store"].humano == {TEL: "comprobante"}
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_COMPROBANTE]
    assert entorno["store"].filas[0]["contenido"] == "[imagen] Listo, ya transferí"


def test_documento_en_modo_humano_no_repite_aviso(entorno):
    _mockear_descarga(entorno)
    entorno["store"].humano[TEL] = "eduardo_responde"
    post_firmado(entorno["http"], _media(tipo="document"))
    assert entorno["meta"].call_count == 0
    assert entorno["store"].filas[0]["contenido"] == "[documento]"


def test_imagen_duplicada_se_procesa_una_vez(entorno):
    documento = _mockear_descarga(entorno)
    post_firmado(entorno["http"], _media())
    post_firmado(entorno["http"], _media())
    assert documento.call_count == 1 and entorno["meta"].call_count == 1


def test_descarga_fallida_igual_alerta_y_deriva(entorno):
    documento = _mockear_descarga(entorno, ok=False)
    post_firmado(entorno["http"], _media())
    assert documento.call_count == 0
    assert "No se pudo descargar" in _alertas(entorno["telegram"])[0]
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_COMPROBANTE]


def test_error_al_responder_comprobante_no_tumba_el_webhook(entorno, caplog):
    _mockear_descarga(entorno)
    entorno["meta"].mock(return_value=httpx.Response(500))
    assert post_firmado(entorno["http"], _media()).status_code == 200
    assert "Error procesando imagen" in caplog.text


# --- Webhook de Telegram -------------------------------------------------------
@pytest.mark.parametrize("secreto", [None, "otro"])
def test_telegram_sin_secreto_valido_se_rechaza(entorno, secreto):
    assert _telegram(entorno, _update("/ayuda"), secreto=secreto).status_code == 401
    assert entorno["telegram"].call_count == 0


def test_telegram_json_invalido(entorno):
    r = entorno["http"].post(
        "/telegram/webhook", content=b"{x", headers={"X-Telegram-Bot-Api-Secret-Token": SECRETO_TG}
    )
    assert r.status_code == 400


def test_telegram_otro_chat_se_ignora(entorno):
    assert _telegram(entorno, _update(f"/responder {TEL} hola", chat_id="123")).json() == {"ok": True}
    assert entorno["meta"].call_count == 0 and entorno["telegram"].call_count == 0


def test_responder_contestando_a_la_alerta(entorno):
    citado = {"message_id": 7, "text": f"💬 +{TEL}\n¿Hay lugar hoy?"}
    _telegram(entorno, _update("Hola, soy Eduardo. Sí, a las 5 PM", citado=citado))

    enviado = json.loads(entorno["meta"].calls[0].request.content)
    assert enviado["text"]["body"] == "Hola, soy Eduardo. Sí, a las 5 PM"
    assert enviado["to"] == "524931234567"
    assert entorno["store"].respuestas_a(TEL) == ["Hola, soy Eduardo. Sí, a las 5 PM"]  # queda en el historial
    assert entorno["store"].humano == {TEL: "eduardo_responde"}
    assert _alertas(entorno["telegram"])[0].startswith(f"✅ Enviado a +{TEL}")


def test_responder_contestando_a_un_comprobante_usa_la_leyenda(entorno):
    citado = {"message_id": 8, "caption": f"🧾 +{TEL} envió una imagen"}
    _telegram(entorno, _update("Recibido, nos vemos el jueves", citado=citado))
    assert _enviados(entorno["meta"]) == ["Recibido, nos vemos el jueves"]


def test_texto_suelto_sin_alerta_citada_explica_como_responder(entorno):
    _telegram(entorno, _update("hola"))
    assert entorno["meta"].call_count == 0
    assert "contesta (reply)" in _alertas(entorno["telegram"])[0]


def test_comando_responder(entorno):
    _telegram(entorno, _update("/responder +52-493-123-4567 Hola, te escribe Eduardo"))
    assert _enviados(entorno["meta"]) == ["Hola, te escribe Eduardo"]
    assert entorno["store"].humano == {TEL: "eduardo_responde"}


@pytest.mark.parametrize(
    "texto, error",
    [
        ("/responder", "Falta un teléfono"),
        (f"/responder {TEL}", "Falta el mensaje"),
        ("/responder 12 hola", "Falta un"),
    ],
)
def test_comando_responder_incompleto(entorno, texto, error):
    _telegram(entorno, _update(texto))
    assert entorno["meta"].call_count == 0
    assert error in _alertas(entorno["telegram"])[0]


def test_responder_fuera_de_ventana_avisa_a_eduardo(entorno):
    entorno["meta"].mock(return_value=httpx.Response(400, json={"error": {"code": 131047}}))
    _telegram(entorno, _update(f"/responder {TEL} hola"))
    alerta = _alertas(entorno["telegram"])[0]
    assert "❌ No se pudo enviar" in alerta and "24 horas" in alerta
    assert entorno["store"].respuestas_a(TEL) == []


def test_reanudar_devuelve_la_conversacion_a_sofia(entorno):
    entorno["store"].humano[TEL] = "comprobante"
    entorno["store"].pausadas[TEL] = "crisis"
    _telegram(entorno, _update("/reanudar@SofiaBot 524931234567"))  # sin el 1 y con @bot
    assert entorno["store"].humano == {} and entorno["store"].pausadas == {}
    assert _alertas(entorno["telegram"])[0] == f"▶️ Sofía vuelve a atender a +{TEL}"

    post_firmado(entorno["http"], payload(texto="Hola de nuevo"))
    assert len(entorno["claude"].peticiones) == 1  # Sofía vuelve a responder


def test_pausar_manual(entorno):
    _telegram(entorno, _update(f"/pausar {TEL}"))
    assert entorno["store"].humano == {TEL: "pausa_manual"}
    assert "⏸️" in _alertas(entorno["telegram"])[0]


@pytest.mark.parametrize("comando", ["/reanudar", "/pausar"])
def test_reanudar_y_pausar_sin_telefono(entorno, comando):
    _telegram(entorno, _update(comando))
    assert "Falta un teléfono válido" in _alertas(entorno["telegram"])[0]


@pytest.mark.parametrize("comando", ["/reanudar", "/pausar"])
def test_reanudar_y_pausar_con_falla_de_base(entorno, comando, monkeypatch):
    async def falla(*_):
        return False

    monkeypatch.setattr(comandos.store, "reanudar", falla)
    monkeypatch.setattr(comandos.store, "activar_humano", falla)
    _telegram(entorno, _update(f"{comando} {TEL}"))
    assert "❌ No se pudo" in _alertas(entorno["telegram"])[0]


@pytest.mark.parametrize("texto", ["/ayuda", "/start", "/otra"])
def test_ayuda(entorno, texto):
    _telegram(entorno, _update(texto))
    assert "/reanudar <teléfono>" in _alertas(entorno["telegram"])[0]


def test_update_sin_texto_se_ignora(entorno):
    _telegram(entorno, {"update_id": 1, "message": {"chat": {"id": 999}, "sticker": {}}})
    assert entorno["telegram"].call_count == 0


# --- numero_meta y transporte de Telegram ----------------------------------------
@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("+52 1 493 123 4567", TEL),
        ("524931234567", TEL),  # MX sin el 1
        ("573001234567", "573001234567"),
        ("12345", None),
        ("", None),
    ],
)
def test_numero_meta(entrada, esperado):
    assert comandos.numero_meta(entrada) == esperado


def test_secreto_vacio_rechaza_todo(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", "")
    assert telegram.secreto_valido("") is False and telegram.secreto_valido("x") is False


@pytest.mark.anyio
async def test_alerta_larga_se_recorta(entorno):
    await telegram.alertar("a" * 5000)
    assert len(_alertas(entorno["telegram"])[0]) == telegram.LIMITE_TEXTO + 1


@pytest.mark.anyio
async def test_enviar_archivo_sin_configurar_o_con_error(entorno, monkeypatch, caplog):
    entorno["respx"].post(URL_DOCUMENTO).mock(return_value=httpx.Response(500))
    assert await telegram.enviar_archivo(b"x", "a.jpg", "image/jpeg", "leyenda") is False
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    assert await telegram.enviar_archivo(b"x", "a.jpg", "image/jpeg", "leyenda") is False
    assert "ARCHIVO sin Telegram configurado" in caplog.text


@pytest.mark.anyio
async def test_registrar_webhook(entorno):
    ruta = (
        entorno["respx"]
        .post("https://api.telegram.org/botbot-token/setWebhook")
        .mock(return_value=httpx.Response(200, json={"ok": True, "result": True}))
    )
    assert await telegram.registrar_webhook("https://x.ngrok.app/telegram/webhook") == {"ok": True, "result": True}
    enviado = json.loads(ruta.calls[0].request.content)
    assert enviado["secret_token"] == SECRETO_TG and enviado["allowed_updates"] == ["message"]
