"""Día 9: métricas por respuesta (latencia, tokens, costo, fase) sin datos de la persona."""

from types import SimpleNamespace

import pytest

from app import config, metricas, store
from tests.conftest import payload, post_firmado
from tests.falsos import SALUDO as SALUDO_CRUDO
from tests.falsos import _enviados, _texto

TEL = "5214931234567"
SALUDO = SALUDO_CRUDO.replace(":", ",")  # limpiar_texto quita los dos puntos


@pytest.fixture(autouse=True)
def modelo_conocido(monkeypatch):
    monkeypatch.setattr(config, "CLAUDE_MODEL", "claude-haiku-5-5")
    monkeypatch.setattr(config, "CLAUDE_MODEL_CLASIFICADOR", "claude-haiku-5-5")


def test_una_fila_con_el_uso_del_clasificador_y_de_la_respuesta(entorno):
    post_firmado(entorno["http"], payload(texto="Hola", wa_id="w1"))

    (fila,) = entorno["store"].metricas
    assert fila["fase"] == "conversacion"
    assert fila["modelo"] == "claude-haiku-5-5"
    assert fila["llamadas_claude"] == 2
    assert (fila["tokens_entrada"], fila["tokens_salida"]) == (240, 30)
    assert fila["costo_usd"] == pytest.approx(0.000039)
    assert fila["latencia_ms"] >= 0


def test_la_fila_no_lleva_datos_de_la_persona(entorno):
    post_firmado(entorno["http"], payload(texto="Hola", wa_id="w1"))

    (fila,) = entorno["store"].metricas
    assert TEL not in str(fila)
    assert SALUDO not in str(fila)
    assert "Hola" not in str(fila)


def test_un_reintento_del_validador_cuenta_en_llamadas_y_costo(entorno):
    entorno["claude"].respuestas = [_texto("**Hola**"), _texto(SALUDO)]

    post_firmado(entorno["http"], payload(texto="Hola", wa_id="w1"))

    (fila,) = entorno["store"].metricas
    assert fila["llamadas_claude"] == 3
    assert fila["tokens_entrada"] == 360


def test_crisis_se_mide_sin_generar_respuesta(entorno):
    entorno["claude"].veredicto = "RIESGO"

    post_firmado(entorno["http"], payload(texto="ya no aguanto", wa_id="w1"))

    (fila,) = entorno["store"].metricas
    assert fila["fase"] == "crisis"
    assert fila["llamadas_claude"] == 1


def test_derivacion(entorno):
    entorno["claude"].respuestas = [
        [
            {"type": "text", "text": "Con gusto, le paso tu mensaje."},
            {
                "type": "tool_use",
                "id": "t1",
                "name": "derivar_a_eduardo",
                "input": {"motivo": "pide_hablar_con_eduardo", "resumen": "Pide hablar con él."},
            },
        ]
    ]

    post_firmado(entorno["http"], payload(texto="Quiero hablar con Eduardo", wa_id="w1"))

    assert entorno["store"].metricas[0]["fase"] == "derivacion"


def test_modo_humano_no_genera_metrica(entorno):
    entorno["store"].humano[TEL] = "comprobante"

    post_firmado(entorno["http"], payload(texto="Hola", wa_id="w1"))

    assert entorno["store"].metricas == []


def test_si_falla_la_metrica_la_persona_recibe_su_respuesta(entorno, monkeypatch):
    async def falla(_fila):
        raise RuntimeError("sin tabla")

    monkeypatch.setattr(store, "guardar_metrica", falla)

    post_firmado(entorno["http"], payload(texto="Hola", wa_id="w1"))

    assert _enviados(entorno["meta"]) == [SALUDO]


@pytest.mark.parametrize(
    ("texto", "derivacion", "fase"),
    [
        ("¿Qué te trae por aquí?", None, "conversacion"),
        ("La sesión cuesta $300 MXN", None, "propuesta"),
        ("Tu CLABE es 012180001234567891", None, "cierre"),
        ("Paga aquí https://buy.stripe.com/x", None, "cierre"),
        ("Le aviso", {"motivo": "otro"}, "derivacion"),
        ("Gracias", {"motivo": "respaldo"}, "respaldo"),
        ("Son 3000 cosas", None, "conversacion"),
    ],
)
def test_fase_de(monkeypatch, texto, derivacion, fase):
    monkeypatch.setitem(config.DATOS_PAGO, "clabe", "012180001234567891")
    monkeypatch.setitem(config.DATOS_PAGO, "link_stripe", "https://buy.stripe.com/x")
    assert metricas.fase_de(texto, derivacion) == fase


def test_sin_datos_de_pago_no_todo_es_cierre(monkeypatch):
    monkeypatch.setitem(config.DATOS_PAGO, "clabe", "")
    monkeypatch.setitem(config.DATOS_PAGO, "link_stripe", "")
    assert metricas.fase_de("Hola", None) == "conversacion"


def test_modelo_sin_precio_no_inventa_costo():
    metricas.iniciar()
    uso = SimpleNamespace(input_tokens=10, output_tokens=5)
    metricas.sumar_uso("claude-haiku-5-5", uso)
    metricas.sumar_uso("modelo-nuevo", uso)
    metricas.sumar_uso("claude-haiku-5-5", uso)

    medicion = metricas._actual.get()
    assert medicion.costo_usd is None
    assert medicion.tokens_entrada == 30


def test_sin_medicion_activa_no_hace_nada():
    metricas._actual.set(None)
    metricas.sumar_uso("claude-haiku-5-5", SimpleNamespace(input_tokens=1, output_tokens=1))
    assert metricas._actual.get() is None
