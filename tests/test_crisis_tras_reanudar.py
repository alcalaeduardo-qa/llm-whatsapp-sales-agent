"""Prueba en vivo del 5 de noviembre: tras /reanudar, un "hola" volvía a contar como crisis porque el clasificador
releía la frase de crisis ya atendida. La pausa regresaba y la persona no salía de ahí."""

import asyncio

import pytest

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

    assert [v[-1:] for v in vistos] == [["hola"]]
    assert "ya no quiero vivir" not in " ".join(vistos[0])


def test_tras_reanudar_los_mensajes_de_la_pausa_tampoco_se_releen(entorno, monkeypatch):
    vistos = _clasificador_que_guarda(monkeypatch)
    post_firmado(entorno["http"], payload("text", "ya no quiero vivir", "wamid.1"))
    post_firmado(entorno["http"], payload("text", "sigo muy mal", "wamid.2"))
    asyncio.run(entorno["store"].reanudar(TEL))

    post_firmado(entorno["http"], payload("text", "quiero reservar", "wamid.3"))

    assert [v[-1:] for v in vistos] == [["quiero reservar"]]
    assert "sigo muy mal" not in " ".join(vistos[0])


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

    assert guardrails.mensajes_para_crisis(historial, "dos")[-1] == "dos"


def test_el_clasificador_lee_la_pregunta_de_sofia_para_entender_un_si():
    pregunta = "¿Sigues pensando en quitarte la vida?"
    historial = [
        {"role": "user", "content": "bien"},
        {"role": "assistant", "content": pregunta},
        {"role": "user", "content": "Si"},
    ]

    assert guardrails.mensajes_para_crisis(historial, "Si") == [f"{llm.MARCA_SOFIA}{pregunta}", "bien", "Si"]


def test_las_respuestas_fijas_no_prometen_a_eduardo_y_aclaran_que_es_coaching():
    assert "Eduardo" not in guardrails.RESPUESTA_CRISIS + guardrails.RESPUESTA_PAUSA
    assert "coaching de vida" in guardrails.RESPUESTA_CRISIS
    assert "atención psicológica" in guardrails.RESPUESTA_CRISIS


def test_un_pais_sin_telefono_verificado_recibe_el_texto_generico():
    respuesta = guardrails.respuesta_crisis("50499887766")

    assert guardrails.LINEA_DE_LA_VIDA not in respuesta
    assert "llama a emergencias" in respuesta


def test_tras_una_crisis_el_modelo_recibe_la_nota(entorno, monkeypatch):
    _clasificador_que_guarda(monkeypatch)
    contextos = []

    async def generar(historial, correccion=None, contexto=None):
        contextos.append(contexto)
        return llm.Respuesta("Hola, ¿cómo estás hoy?")

    monkeypatch.setattr(llm, "generar_respuesta", generar)
    post_firmado(entorno["http"], payload("text", "ya no quiero vivir", "wamid.1"))
    asyncio.run(entorno["store"].reanudar(TEL))

    post_firmado(entorno["http"], payload("text", "hola", "wamid.2"))

    assert llm.NOTA_TRAS_CRISIS in contextos[0]


def test_el_pais_que_dice_la_persona_manda_sobre_su_prefijo():
    respuesta = guardrails.respuesta_pausa("5214931234567", "estoy en España")

    assert "024" in respuesta and guardrails.LINEA_DE_LA_VIDA not in respuesta


def test_el_aviso_se_repite_en_cada_mensaje_del_bloqueo():
    assert "escribe solamente ACEPTO" in guardrails.respuesta_pausa("5214931234567", "hola")
    assert "no somos las personas indicadas" in guardrails.respuesta_pausa("5214931234567", "soy de Perú")


def test_un_pais_sin_linea_confirmada_recibe_el_texto_generico():
    respuesta = guardrails.respuesta_pausa("5214931234567", "soy de Honduras")

    assert "número local de emergencias" in respuesta and guardrails.LINEA_DE_LA_VIDA not in respuesta


def test_prefijo_dominicano_no_recibe_la_linea_de_estados_unidos():
    assert "988" not in guardrails.respuesta_crisis("18095551234")


def test_durante_la_pausa_no_se_alerta_a_eduardo(entorno):
    entorno["store"].pausadas[TEL] = "crisis"

    post_firmado(entorno["http"], payload("text", "hola", "wamid.1"))

    assert entorno["telegram"].call_count == 0


def test_acepto_en_mayusculas_registra_quita_el_bloqueo_y_pregunta_si_agenda(entorno):
    entorno["store"].pausadas[TEL] = "crisis"

    post_firmado(entorno["http"], payload("text", "ACEPTO", "wamid.1"))

    assert entorno["store"].aceptaciones == [(TEL, "ACEPTO", "wamid.1", guardrails.VERSION_AVISO)]
    assert TEL not in entorno["store"].pausadas
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_ACEPTADO]


@pytest.mark.parametrize("texto", ["acepto", "Acepto", "ACEPTO gracias", "si ACEPTO"])
def test_solo_cuenta_la_palabra_exacta_en_mayusculas(entorno, texto):
    entorno["store"].pausadas[TEL] = "crisis"

    post_firmado(entorno["http"], payload("text", texto, "wamid.1"))

    assert entorno["store"].aceptaciones == []
    assert TEL in entorno["store"].pausadas


def test_sin_registro_guardado_el_bloqueo_sigue(entorno):
    entorno["store"].pausadas[TEL] = "crisis"
    entorno["store"].aceptacion_falla = True

    post_firmado(entorno["http"], payload("text", "ACEPTO", "wamid.1"))

    assert TEL in entorno["store"].pausadas
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_PAUSA]


def test_una_crisis_despues_de_aceptar_vuelve_a_bloquear(entorno, monkeypatch):
    _clasificador_que_guarda(monkeypatch)
    entorno["store"].pausadas[TEL] = "crisis"
    post_firmado(entorno["http"], payload("text", "ACEPTO", "wamid.1"))

    post_firmado(entorno["http"], payload("text", "quiero morir", "wamid.2"))

    assert TEL in entorno["store"].pausadas


@pytest.mark.parametrize("texto", ["me quiero morir de la vergüenza", "quiero morir de risa"])
def test_expresiones_coloquiales_no_son_crisis_explicita(texto):
    assert not guardrails.crisis_explicita(texto)


@pytest.mark.parametrize("texto", ["me quiero morir", "quiero morir ya", "me quiero morir de verdad"])
def test_la_frase_real_sigue_siendo_crisis_explicita(texto):
    assert guardrails.crisis_explicita(texto)
