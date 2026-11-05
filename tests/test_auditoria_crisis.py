"""Auditoría de seguridad (5 de noviembre de 2026): protección de crisis.

Cada test afirma lo que Sofía debe hacer. Nacieron como fallo esperado estricto: fallaban y confirmaban cada
pista con una prueba ejecutada. Ya pasan porque se corrigió el comportamiento, y quedan como regresión.
Todo corre con datos de prueba y dobles.
"""

import httpx

from app import config, guardrails, llm, store, whatsapp
from tests.conftest import payload, post_firmado
from tests.falsos import SALUDO, _alertas, _enviados
from tests.test_audio import _nota_de_voz
from tests.test_derivacion import _media, _mockear_descarga

TEL = "5214931234567"
# La función real, guardada antes de que el fixture `entorno` la cambie por el doble en memoria
OBTENER_ESTADO_REAL = store.obtener_estado
# Respuesta de venta que pasa el validador (sin dos puntos), para que llegue tal cual si se envía
VENTA = SALUDO.replace(":", ",")


def _con_linea_de_ayuda(entorno) -> bool:
    return any(guardrails.LINEA_DE_LA_VIDA in texto for texto in _enviados(entorno["meta"]))


# --- crisis-controls-skipped-on-media-and-failed-audio-paths ---------------------------------------
def test_pie_de_imagen_con_crisis_recibe_la_linea_de_ayuda(entorno):
    _mockear_descarga(entorno)

    post_firmado(entorno["http"], _media(caption="ya no quiero vivir"))

    assert _con_linea_de_ayuda(entorno)


def test_imagen_durante_una_pausa_por_crisis_no_reemplaza_la_contencion(entorno):
    _mockear_descarga(entorno)
    entorno["store"].pausadas[TEL] = "crisis"

    post_firmado(entorno["http"], _media())

    assert _con_linea_de_ayuda(entorno)


def test_nota_de_voz_fallida_durante_una_pausa_por_crisis_no_pide_solo_que_la_escriba(entorno, monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    entorno["store"].pausadas[TEL] = "crisis"

    post_firmado(entorno["http"], _nota_de_voz())

    assert _con_linea_de_ayuda(entorno) or entorno["telegram"].call_count > 0


# --- conversation-gate-read-once-before-model-call-no-per-phone-ordering ---------------------------
def test_no_se_manda_venta_si_una_crisis_pausó_la_conversacion_mientras_se_generaba(entorno, monkeypatch):
    async def generar_y_pausar(historial, correccion=None, contexto=None):
        entorno["store"].pausadas[TEL] = "crisis"  # otra tarea atendió una crisis mientras Claude pensaba
        return llm.Respuesta(VENTA)

    monkeypatch.setattr(llm, "generar_respuesta", generar_y_pausar)

    post_firmado(entorno["http"], payload(texto="¿Cuánto cuesta la sesión?"))

    assert VENTA not in _enviados(entorno["meta"])


def test_la_frase_explicita_del_mensaje_actual_cuenta_aunque_el_historial_traiga_otro_al_final(entorno, monkeypatch):
    entorno["claude"].clasificador_falla = True  # solo quedan las reglas deterministas

    async def historial_con_otro_mensaje_al_final(*_args, **_kwargs):
        return [
            {"role": "user", "content": "me quiero morir"},
            {"role": "user", "content": "cuanto cuesta la sesion"},  # llegó un instante después
        ]

    monkeypatch.setattr(store, "obtener_historial", historial_con_otro_mensaje_al_final)

    post_firmado(entorno["http"], payload(texto="me quiero morir"))

    assert guardrails.RESPUESTA_CRISIS in _enviados(entorno["meta"])


# --- human-mode-crisis-detection-explicit-only -----------------------------------------------------
def test_modo_humano_tambien_revisa_la_crisis_encubierta(entorno):
    entorno["store"].humano[TEL] = "comprobante"
    entorno["claude"].veredicto = "RIESGO"

    post_firmado(entorno["http"], payload(texto="ya no quiero despertar mañana"))

    alerta = _alertas(entorno["telegram"])[0]
    assert alerta.startswith("🚨") or _con_linea_de_ayuda(entorno)


# --- crisis-response-gated-on-persistence-with-unconditional-alert ---------------------------------
def test_crisis_recibe_la_linea_de_ayuda_aunque_falle_el_guardado(entorno, monkeypatch):
    async def base_caida(*_args, **_kwargs):
        raise RuntimeError("supabase caído")

    monkeypatch.setattr(store, "registrar_entrante", base_caida)

    post_firmado(entorno["http"], payload(texto="me quiero morir"))

    assert _con_linea_de_ayuda(entorno)


def test_la_alerta_no_afirma_que_se_pausó_la_venta_si_la_pausa_falló(entorno, monkeypatch):
    async def pausa_fallida(*_args, **_kwargs):
        return False

    monkeypatch.setattr(store, "pausar_venta", pausa_fallida)

    post_firmado(entorno["http"], payload(texto="quiero hacerme daño"))

    assert "detuvo la venta" not in _alertas(entorno["telegram"])[0]


def test_si_falla_claude_alguien_se_entera(entorno, monkeypatch):
    async def claude_caido(*_args, **_kwargs):
        raise RuntimeError("API caída")

    monkeypatch.setattr(llm, "generar_respuesta", claude_caido)

    post_firmado(entorno["http"], payload(texto="Hola, quiero información"))

    assert entorno["meta"].call_count > 0 or entorno["telegram"].call_count > 0


# --- obtener-estado-fails-open-safety-state --------------------------------------------------------
def test_si_no_se_puede_leer_el_estado_no_responde_el_modelo(entorno, monkeypatch):
    monkeypatch.setattr(store, "obtener_estado", OBTENER_ESTADO_REAL)  # la real falla: el cliente está bloqueado

    post_firmado(entorno["http"], payload(texto="¿Cuánto cuesta la sesión?"))

    assert entorno["claude"].peticiones == []


# --- webhook-signed-batch-items-after-first-dropped ------------------------------------------------
def test_un_lote_firmado_se_procesa_completo(entorno):
    lote = payload(texto="Hola uno", wa_id="wamid.UNO")
    lote["entry"][0]["changes"][0]["value"]["messages"].append(
        {"from": TEL, "id": "wamid.DOS", "type": "text", "text": {"body": "Hola dos"}}
    )

    post_firmado(entorno["http"], lote)

    recibidos = [f["contenido"] for f in entorno["store"].filas if f["rol"] == "user"]
    assert recibidos == ["Hola uno", "Hola dos"]


# --- telegram.alertar:human-mode-transcript-tail-dropped -------------------------------------------
def test_en_modo_humano_el_final_de_un_mensaje_largo_llega_a_eduardo(entorno):
    entorno["store"].humano[TEL] = "comprobante"

    post_firmado(entorno["http"], payload(texto="A" * 4000 + " FIN-DEL-MENSAJE"))

    assert any("FIN-DEL-MENSAJE" in alerta for alerta in _alertas(entorno["telegram"]))


# --- procesar_media:enviar-archivo-false-ignored ---------------------------------------------------
def test_si_telegram_rechaza_el_comprobante_eduardo_recibe_un_aviso(entorno):
    documento = _mockear_descarga(entorno)
    documento.mock(return_value=httpx.Response(500))

    post_firmado(entorno["http"], _media(caption="Listo, ya transferí"))

    assert len(_alertas(entorno["telegram"])) >= 1


# --- Caminos de emergencia de las correcciones -------------------------------------------------------
def test_con_el_estado_ilegible_una_crisis_igual_recibe_la_linea_de_ayuda(entorno, monkeypatch):
    monkeypatch.setattr(store, "obtener_estado", OBTENER_ESTADO_REAL)

    post_firmado(entorno["http"], payload(texto="me quiero morir"))

    assert _con_linea_de_ayuda(entorno) and entorno["claude"].peticiones == []


def test_si_falla_el_guardado_y_tambien_whatsapp_eduardo_igual_se_entera(entorno, monkeypatch):
    async def base_caida(*_args, **_kwargs):
        raise RuntimeError("supabase caído")

    monkeypatch.setattr(store, "registrar_entrante", base_caida)
    entorno["meta"].mock(return_value=httpx.Response(500))

    post_firmado(entorno["http"], payload(texto="me quiero morir"))

    assert _alertas(entorno["telegram"])[0].startswith("🚨")


def test_si_falla_el_guardado_un_mensaje_normal_no_se_responde_y_eduardo_se_entera(entorno, monkeypatch):
    async def base_caida(*_args, **_kwargs):
        raise RuntimeError("supabase caído")

    monkeypatch.setattr(store, "registrar_entrante", base_caida)

    post_firmado(entorno["http"], payload(texto="Hola, quiero información"))

    assert entorno["meta"].call_count == 0 and entorno["claude"].peticiones == []
    assert "No pude guardar el mensaje" in _alertas(entorno["telegram"])[0]


def test_la_respuesta_de_crisis_sale_aunque_falle_guardarla(entorno, monkeypatch):
    async def no_guarda(*_args, **_kwargs):
        raise RuntimeError("supabase caído")

    monkeypatch.setattr(store, "guardar_respuesta", no_guarda)

    post_firmado(entorno["http"], payload(texto="quiero hacerme daño"))

    assert _con_linea_de_ayuda(entorno)


def test_si_el_estado_se_vuelve_ilegible_justo_antes_de_enviar_la_respuesta_sale(entorno, monkeypatch):
    lecturas = []
    original = entorno["store"].obtener_estado

    async def segunda_lectura_ilegible(telefono):
        lecturas.append(telefono)
        if len(lecturas) == 1:
            return await original(telefono)
        return {"venta_pausada": False, "humano_activo": False, "atendida_por_eduardo": False, "desconocido": True}

    monkeypatch.setattr(store, "obtener_estado", segunda_lectura_ilegible)

    post_firmado(entorno["http"], payload(texto="Hola"))

    assert SALUDO.replace(":", ",") in _enviados(entorno["meta"])


def test_si_eduardo_toma_la_conversacion_mientras_se_genera_la_respuesta_se_la_reenvian_a_el(entorno, monkeypatch):
    async def generar_y_tomar(historial, correccion=None, contexto=None):
        entorno["store"].humano[TEL] = "eduardo_responde"  # Eduardo escribió con /pausar o contestando una alerta
        return llm.Respuesta(VENTA)

    monkeypatch.setattr(llm, "generar_respuesta", generar_y_tomar)

    post_firmado(entorno["http"], payload(texto="¿Cuánto cuesta la sesión?"))

    assert VENTA not in _enviados(entorno["meta"])
    assert "¿Cuánto cuesta la sesión?" in _alertas(entorno["telegram"])[0]


def test_el_clasificador_siempre_ve_el_mensaje_actual_aunque_la_historia_no_lo_traiga(entorno, monkeypatch):
    async def historia_vieja(*_args, **_kwargs):
        return [{"role": "user", "content": "hola"}]

    monkeypatch.setattr(store, "obtener_historial", historia_vieja)

    post_firmado(entorno["http"], payload(texto="Quiero información"))

    assert "Quiero información" in entorno["claude"].clasificaciones[0]["messages"][0]["content"]


def test_pie_de_imagen_con_crisis_en_modo_humano_marca_la_alerta_y_manda_la_linea_de_ayuda(entorno):
    _mockear_descarga(entorno)
    entorno["store"].humano[TEL] = "comprobante"

    post_firmado(entorno["http"], _media(caption="ya no quiero vivir"))

    assert _con_linea_de_ayuda(entorno)
    assert any(alerta.startswith("🚨") for alerta in _alertas(entorno["telegram"]))


def test_un_lote_con_elementos_mal_formados_procesa_los_buenos():
    cuerpo = {
        "entry": [
            "no soy un dict",
            {
                "changes": [
                    None,
                    {
                        "value": {
                            "messages": [
                                {"from": "1"},
                                {"from": "1", "id": "w", "type": "text", "text": {"body": "ok"}},
                            ]
                        }
                    },
                ]
            },
        ]
    }

    assert [m["text"] for m in whatsapp.extraer_mensajes(cuerpo)] == ["ok"]
    assert whatsapp.extraer_mensajes({"entry": None}) == [] and whatsapp.extraer_mensajes({}) == []
