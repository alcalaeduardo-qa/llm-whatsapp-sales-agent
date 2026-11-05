"""Webhook de punta a punta con los dobles de tests/falsos.py (Meta y Telegram con respx,
Claude con un transporte falso de httpx2 y un store en memoria)."""

import json
import logging

import httpx
import pytest

from app import guardrails
from app.text import huella
from tests.conftest import payload, post_firmado
from tests.falsos import _alertas, _enviados, _texto


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
    assert (
        entorno["claude"].clasificaciones[1]["messages"][0]["content"]
        == "<mensajes>\nHola\nTengo ansiedad\n</mensajes>"
    )


def test_evento_de_estado_no_llama_a_nadie(entorno):
    datos = {"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]}
    assert post_firmado(entorno["http"], datos).json() == {"status": "ignorado"}
    assert entorno["claude"].peticiones == [] and entorno["meta"].call_count == 0


@pytest.mark.parametrize("tipo", ["audio", "sticker", "reaction"])
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
    assert f"Error procesando mensaje #{huella('wamid.ABC')}" in caplog.text


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
    entorno["claude"].veredicto = "RIESGO"
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
    assert "Derivación de +5214931234567 (respaldo)" in alerta and "palabra prohibida terapia" in alerta
    assert entorno["store"].humano == {"5214931234567": "respaldo"}  # el respaldo promete que Eduardo escribe
    assert entorno["store"].filas[-1]["contenido"] == guardrails.RESPUESTA_RESPALDO


def test_claude_sin_texto_termina_en_respaldo(entorno):
    entorno["claude"].respuestas = [[]]
    post_firmado(entorno["http"], payload())
    assert len(entorno["claude"].peticiones) == 2
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_RESPALDO]


# --- Parámetros de la API (bug real: el razonamiento se comía max_tokens) -------------
def test_generacion_pide_esfuerzo_bajo_y_espacio_para_razonar(entorno):
    post_firmado(entorno["http"], payload())
    peticion = entorno["claude"].peticiones[0]
    assert peticion["output_config"] == {"effort": "low"}
    assert peticion["max_tokens"] >= 4000


def test_clasificador_sin_razonamiento(entorno):
    post_firmado(entorno["http"], payload())
    clasificacion = entorno["claude"].clasificaciones[0]
    assert clasificacion["thinking"] == {"type": "disabled"}
    assert clasificacion["max_tokens"] >= 64


@pytest.mark.parametrize("texto, crisis", [("ya no puedo más", True), ("¿cuánto cuesta?", False)])
def test_clasificador_cortado_no_cuenta_como_no(entorno, texto, crisis):
    """Si el clasificador no termina normal, se usa la lista de señales en vez de asumir NO."""
    entorno["claude"].clasificador_stop = "max_tokens"
    post_firmado(entorno["http"], payload(texto=texto))
    assert (entorno["store"].pausadas != {}) is crisis


# --- Clasificador: etiquetas RIESGO/NORMAL (bug encontrado por los evals) ----------------------------
def test_clasificador_recibe_los_mensajes_como_datos(entorno):
    post_firmado(entorno["http"], payload(texto="¿Eres un bot?"))
    contenido = entorno["claude"].clasificaciones[0]["messages"][0]["content"]
    assert contenido.startswith("<mensajes>") and contenido.endswith("</mensajes>")


@pytest.mark.parametrize("veredicto", ["Sí", "SI", "No sé"])
def test_respuesta_que_no_es_etiqueta_no_cuenta_como_crisis(entorno, veredicto):
    """Con SI/NO, el modelo contestaba la pregunta de la persona ("¿hablo con una IA?" → "Sí")."""
    entorno["claude"].veredicto = veredicto
    post_firmado(entorno["http"], payload(texto="Dime la verdad, ¿estoy hablando con una inteligencia artificial?"))
    assert entorno["store"].pausadas == {}  # sin señales de riesgo: sigue la conversación normal
    assert len(entorno["claude"].peticiones) == 1


def test_respuesta_que_no_es_etiqueta_con_senal_ambigua_si_es_crisis(entorno):
    entorno["claude"].veredicto = "Sí"
    post_firmado(entorno["http"], payload(texto="Ya no puedo más con todo esto"))
    assert entorno["store"].pausadas == {"5214931234567": "crisis"}


def test_respuesta_solo_larga_se_envia_sin_respaldo(entorno):
    """Caso del eval: una propuesta de 1140 caracteres dos veces terminaba en respaldo y derivaba."""
    larga = "Te cuento cómo funciona la sesión con Eduardo. " * 30
    entorno["claude"].respuestas = [_texto(larga)]
    post_firmado(entorno["http"], payload())
    assert len(entorno["claude"].peticiones) == 2  # se reintentó una vez
    assert _enviados(entorno["meta"]) == [larga.strip()]
    assert entorno["store"].humano == {}  # no derivó


def test_larga_y_con_otro_problema_si_usa_respaldo(entorno):
    entorno["claude"].respuestas = [_texto("Esto es terapia. " * 80)]
    post_firmado(entorno["http"], payload())
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_RESPALDO]
