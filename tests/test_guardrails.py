import json

import httpx
import pytest
import respx

from app import config, guardrails, telegram
from app.guardrails import (
    RESPUESTA_CRISIS,
    RESPUESTA_PAUSA,
    RESPUESTA_RESPALDO,
    crisis_explicita,
    detectar_crisis,
    senal_debil,
    validar_salida,
)

BUENA = (
    "Hola, soy Sofía, asistente de Eduardo en Sanando el Alma. Te aviso que Eduardo acompaña en sesiones "
    "privadas de pago.\n\n¿De qué país me escribes?"
)


# --- Validador de salida ---------------------------------------------------
def test_respuesta_correcta_pasa():
    assert validar_salida(BUENA) == []


@pytest.mark.parametrize("fija", [RESPUESTA_CRISIS, RESPUESTA_PAUSA, RESPUESTA_RESPALDO])
def test_respuestas_fijas_cumplen_sus_propias_reglas(fija):
    assert validar_salida(fija) == []


def test_mensaje_de_crisis_incluye_linea_de_la_vida():
    assert "800 911 2000" in RESPUESTA_CRISIS and "800 911 2000" in RESPUESTA_PAUSA


@pytest.mark.parametrize(
    "texto, problema",
    [
        ('Eso que llamas "amor" duele', "comillas"),
        ("Mira esto «así»", "comillas"),
        ("Espera... piensa", "puntos suspensivos"),
        ("Espera… piensa", "puntos suspensivos"),
        ("Es clave — piensa", "guiones"),
        ("Tu auto-estima importa", "guiones"),
        ("Te propongo esto: hablar", "dos puntos"),
        ("Hoy no; mañana sí", "punto y coma"),
        ("Esto es **importante**", "markdown o lista"),
        ("Opciones\n1. hoy\n2. mañana", "markdown o lista"),
        ("Opciones\n• hoy", "markdown o lista"),
        ("## Fase 2", "markdown o lista"),
        ("", "vacío"),
    ],
)
def test_signos_y_formato_prohibidos(texto, problema):
    assert problema in validar_salida(texto)


@pytest.mark.parametrize(
    "texto, palabra",
    [
        ("Esto no es terapia", "terapia"),
        ("Eduardo no es tu terapeuta", "terapeuta"),
        ("Una PSICÓLOGA te diría", "psicologa"),
        ("Muchos pacientes llegan así", "pacientes"),
        ("No damos tratamiento", "tratamiento"),
        ("Sin diagnósticos", "diagnosticos"),
        ("No es un trastorno", "trastorno"),
        ("No hay patología", "patologia"),
        ("Un enfoque clínico", "clinico"),
        ("Agenda tu consulta psicológica", "consulta psicologica"),
    ],
)
def test_palabras_prohibidas_sin_importar_acentos_ni_mayusculas(texto, palabra):
    assert f"palabra prohibida {palabra}" in validar_salida(texto)


@pytest.mark.parametrize("texto", ["Tienes mucha paciencia contigo", "Lo que sientes es tratable"])
def test_palabras_parecidas_no_se_confunden(texto):
    assert validar_salida(texto) == []


@pytest.mark.parametrize(
    "texto",
    [
        "La inversión es de $300 pesos mexicanos",
        "La inversión es de $ 300.00",
        "Cuesta 300 MXN",
        "La guía cuesta 136 pesos mexicanos o 8 USD",
        "Llama al 800 911 2000 o escribe a las 2 PM",
        "Transferencia a CLABE 012345678901234567",
        "Una hora con Eduardo, atiende las 24 horas",
    ],
)
def test_precios_y_numeros_permitidos(texto):
    assert validar_salida(texto) == []


@pytest.mark.parametrize(
    "texto",
    [
        "Hoy te lo dejo en $250",
        "Son 500 pesos",
        "Equivale a 17 dólares",
        "Son 15 USD",
        "Son 70000 COP",
        "Te cuesta 1,500 pesos",
    ],
)
def test_precios_no_autorizados(texto):
    assert any(p.startswith("precio no autorizado") for p in validar_salida(texto))


def test_enlaces_quedan_exentos_de_signos():
    texto = "Paga en esta página segura https://buy.stripe.com/a-b_c:d?x=1;y=2 y me avisas"
    assert validar_salida(texto) == []


def test_longitud_y_parrafos(monkeypatch):
    monkeypatch.setattr(config, "MAX_CARACTERES", 50)
    assert "demasiado largo" in validar_salida("a" * 51)[0]
    monkeypatch.setattr(config, "MAX_CARACTERES", 900)
    assert validar_salida("\n\n".join(["Uno.", "Dos.", "Tres.", "Cuatro."])) == []
    assert "demasiados párrafos (5)" in validar_salida("\n\n".join(["a", "b", "c", "d", "e"]))


# --- Detección de crisis -----------------------------------------------------
@pytest.mark.parametrize(
    "texto",
    [
        "Ya pensé en el suicidio",
        "a veces quiero quitarme la vida",
        "Me quiero morir",
        "no quiero seguir viviendo así",
        "Quiero hacerme daño",
        "anoche volví a cortarme",
        "todos estarían mejor sin mí",
        "Voy a tomarme todas las pastillas",
        "mi pareja me pega cuando toma",
        "QUIERO ACABAR CON TODO",
    ],
)
def test_crisis_explicita(texto):
    assert crisis_explicita(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "Me siento muy triste por mi ruptura",
        "Tengo ansiedad todo el día",
        "Mi papá murió hace un año",
        "Me muero de ganas de empezar",  # coloquial, no es riesgo
        "¿Cuánto cuesta la sesión?",
    ],
)
def test_no_es_crisis_explicita(texto):
    assert not crisis_explicita(texto)


def test_senal_debil():
    assert senal_debil("Ya no puedo más con esto")
    assert senal_debil("A veces pienso en desaparecer")
    assert not senal_debil("Tengo ansiedad")


@pytest.mark.anyio
async def test_explicita_no_consulta_al_modelo(monkeypatch):
    async def no_llamar(_):
        raise AssertionError("no debía llamar al clasificador")

    monkeypatch.setattr(guardrails.llm, "clasificar_crisis", no_llamar)
    assert await detectar_crisis(["me quiero morir"])


@pytest.mark.anyio
@pytest.mark.parametrize("veredicto", [True, False])
async def test_sin_palabras_explicitas_decide_el_clasificador(monkeypatch, veredicto):
    recibidos = []

    async def clasificar(mensajes):
        recibidos.append(mensajes)
        return veredicto

    monkeypatch.setattr(guardrails.llm, "clasificar_crisis", clasificar)
    assert await detectar_crisis(["hola", "ya les dejé cartas a todos"]) is veredicto
    assert recibidos == [["hola", "ya les dejé cartas a todos"]]


@pytest.mark.anyio
@pytest.mark.parametrize("texto, esperado", [("ya no puedo más", True), ("hola, ¿cuánto cuesta?", False)])
async def test_falla_del_clasificador_usa_senales(monkeypatch, texto, esperado):
    async def roto(_):
        raise RuntimeError("API caída")

    monkeypatch.setattr(guardrails.llm, "clasificar_crisis", roto)
    assert await detectar_crisis([texto]) is esperado


@pytest.mark.anyio
async def test_clasificador_desactivado_usa_senales(monkeypatch):
    monkeypatch.setattr(config, "CRISIS_LLM", False)
    assert await detectar_crisis(["no aguanto más"]) is True
    assert await detectar_crisis(["tengo dudas del pago"]) is False


# --- Telegram ----------------------------------------------------------------
URL_TELEGRAM = "https://api.telegram.org/botbot-token/sendMessage"


@pytest.mark.anyio
async def test_alerta_telegram_ok():
    with respx.mock as mock:
        ruta = mock.post(URL_TELEGRAM).mock(return_value=httpx.Response(200, json={"ok": True}))
        assert await telegram.alertar("prueba") is True
    assert json.loads(ruta.calls[0].request.content) == {"chat_id": "999", "text": "prueba"}


@pytest.mark.anyio
async def test_alerta_telegram_error_no_lanza(caplog):
    with respx.mock as mock:
        mock.post(URL_TELEGRAM).mock(return_value=httpx.Response(500))
        assert await telegram.alertar("prueba") is False
    assert "bot-token" not in caplog.text  # el token no se filtra a los logs


@pytest.mark.anyio
async def test_alerta_sin_configurar(monkeypatch, caplog):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    assert await telegram.alertar("prueba") is False
    assert "ALERTA sin Telegram configurado" in caplog.text
