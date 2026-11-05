"""Prueba en vivo del 5 de noviembre: tras /reanudar, un "hola" volvía a contar como crisis porque el clasificador
releía la frase de crisis ya atendida. La pausa regresaba y la persona no salía de ahí."""

import asyncio

from app import guardrails, llm
from tests.conftest import payload, post_firmado
from tests.falsos import _enviados

TEL = "5214931234567"


def _clasificador_que_guarda(monkeypatch, riesgo=False):
    vistos = []

    async def clasificar(mensajes):
        vistos.append(list(mensajes))
        return riesgo

    monkeypatch.setattr(llm, "clasificar_crisis", clasificar)
    return vistos


def test_tras_reanudar_el_clasificador_no_relee_la_crisis_ya_atendida(entorno, monkeypatch):
    vistos = _clasificador_que_guarda(monkeypatch)
    post_firmado(entorno["http"], payload("text", "ya no quiero vivir", "wamid.1"))
    asyncio.run(entorno["store"].reanudar(TEL))

    post_firmado(entorno["http"], payload("text", "hola", "wamid.2"))

    assert vistos == [["hola"]]


def test_tras_reanudar_los_mensajes_de_la_pausa_tampoco_se_releen(entorno, monkeypatch):
    vistos = _clasificador_que_guarda(monkeypatch)
    post_firmado(entorno["http"], payload("text", "ya no quiero vivir", "wamid.1"))
    post_firmado(entorno["http"], payload("text", "sigo muy mal", "wamid.2"))
    asyncio.run(entorno["store"].reanudar(TEL))

    post_firmado(entorno["http"], payload("text", "quiero reservar", "wamid.3"))

    assert vistos == [["quiero reservar"]]


def test_una_crisis_nueva_tras_reanudar_se_sigue_atendiendo(entorno, monkeypatch):
    _clasificador_que_guarda(monkeypatch)
    post_firmado(entorno["http"], payload("text", "ya no quiero vivir", "wamid.1"))
    asyncio.run(entorno["store"].reanudar(TEL))

    post_firmado(entorno["http"], payload("text", "quiero morir", "wamid.2"))

    assert _enviados(entorno["meta"]).count(guardrails.RESPUESTA_CRISIS) == 2


def test_sin_crisis_previa_el_clasificador_conserva_el_contexto():
    historial = [{"role": "user", "content": t} for t in ("uno", "dos", "tres", "cuatro")]

    assert guardrails.mensajes_para_crisis(historial, "cuatro") == ["dos", "tres", "cuatro"]


def test_el_mensaje_actual_siempre_se_clasifica():
    historial = [{"role": "user", "content": "uno"}, {"role": "assistant", "content": guardrails.RESPUESTA_PAUSA}]

    assert guardrails.mensajes_para_crisis(historial, "dos") == ["dos"]
