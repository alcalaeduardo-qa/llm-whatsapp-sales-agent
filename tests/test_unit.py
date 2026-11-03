from datetime import datetime, timedelta

import pytest
from freezegun import freeze_time

from app import config, prompt
from app.prompt import cargar_prompt, renderizar
from app.scheduling import calcular_horarios, dia_y_fecha, fecha_hora_legible
from app.store import preparar_historial
from app.text import huella, limpiar_texto, normalizar_numero
from app.whatsapp import extraer_mensaje, firma_valida

MX = config.ZONA_MX


# --- normalizar_numero ---------------------------------------------------
@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("5214931234567", "524931234567"),  # móvil MX con 1
        ("524931234567", "524931234567"),  # ya normalizado
        ("573001234567", "573001234567"),  # Colombia, sin cambios
        ("584121234567", "584121234567"),  # Venezuela, sin cambios
        ("52149312345", "52149312345"),  # empieza con 521 pero longitud incorrecta
        ("", ""),
    ],
)
def test_normalizar_numero(entrada, esperado):
    assert normalizar_numero(entrada) == esperado


# --- limpiar_texto (regresión del NameError por falta de `import re`) ----
def test_limpiar_texto_quita_signos_prohibidos():
    texto = "Hola “amiga”... mira esto — es clave: respira; ya"
    assert limpiar_texto(texto) == "Hola amiga. mira esto, es clave, respira, ya"


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ('Dijo "basta"', "Dijo basta"),
        ("«Suelta» eso", "Suelta eso"),
        ("Espera… ya", "Espera. ya"),
        ("uno – dos - tres", "uno, dos, tres"),
        ("auto-estima y co-dependencia", "auto-estima y co-dependencia"),  # guion sin espacios se respeta
        ("Son las 2:30 PM", "Son las 2:30 PM"),  # dos puntos sin espacio se respetan
        ("  con espacios  ", "con espacios"),
        ("", ""),
    ],
)
def test_limpiar_texto_casos(entrada, esperado):
    assert limpiar_texto(entrada) == esperado


def test_limpiar_texto_respeta_enlaces():
    assert limpiar_texto("Paga aquí https://buy.stripe.com/abc") == "Paga aquí https://buy.stripe.com/abc"


def test_huella_es_estable_y_corta():
    assert huella("hola") == huella("hola") != huella("Hola")
    assert len(huella("hola")) == 10


# --- calcular_horarios (freezegun congela el reloj; MX = UTC-6 sin horario de verano) ---
def _ahora_mx():
    return datetime.now(MX)


def test_regresion_domingo_de_madrugada_dice_lunes():
    # Bug real: con "Sunday" en inglés y "mañana" sin fecha, Claude dijo "mañana martes"
    ahora = datetime(2026, 11, 1, 0, 57, tzinfo=MX)
    assert fecha_hora_legible(ahora) == "domingo 1 de noviembre de 2026, 12:57 AM"
    assert "mañana lunes 2 de noviembre a las 2 PM" in calcular_horarios(ahora)


def test_fechas_cruzan_fin_de_mes_y_de_año():
    ahora = datetime(2026, 12, 31, 19, tzinfo=MX)
    assert calcular_horarios(ahora) == "mañana viernes 1 de enero a las 2 PM o el sábado 2 de enero a las 2 PM"
    assert dia_y_fecha(datetime(2027, 2, 28).date()) == "domingo 28 de febrero"


@pytest.mark.parametrize(
    "instante_mx, esperado",
    [
        # Viernes 30/10/2026
        ("2026-10-30 09:00", "hoy viernes 30 de octubre a las 2 PM o mañana sábado 31 de octubre a las 2 PM"),
        # recién pasada la medianoche
        ("2026-10-30 00:30", "hoy viernes 30 de octubre a las 2 PM o mañana sábado 31 de octubre a las 2 PM"),
        ("2026-10-30 11:59", "hoy viernes 30 de octubre a las 2 PM o mañana sábado 31 de octubre a las 2 PM"),
        # 12+2 = 14 exacto, válido
        ("2026-10-30 12:00", "hoy viernes 30 de octubre a las 2 PM o mañana sábado 31 de octubre a las 2 PM"),
        # un minuto tarde, salta a 3 PM
        ("2026-10-30 12:01", "hoy viernes 30 de octubre a las 3 PM o mañana sábado 31 de octubre a las 2 PM"),
        ("2026-10-30 15:30", "hoy viernes 30 de octubre a las 6 PM o mañana sábado 31 de octubre a las 2 PM"),
        # última sesión del día
        ("2026-10-30 17:00", "hoy viernes 30 de octubre a las 7 PM o mañana sábado 31 de octubre a las 2 PM"),
        ("2026-10-30 17:01", "mañana sábado 31 de octubre a las 2 PM o el domingo 1 de noviembre a las 2 PM"),
        ("2026-10-30 23:59", "mañana sábado 31 de octubre a las 2 PM o el domingo 1 de noviembre a las 2 PM"),
        # Fin de semana: hoy se ofrecen sábado y domingo (el horario 2–8 PM aplica todos los días)
        ("2026-10-31 10:00", "hoy sábado 31 de octubre a las 2 PM o mañana domingo 1 de noviembre a las 2 PM"),
        ("2026-10-31 18:00", "mañana domingo 1 de noviembre a las 2 PM o el lunes 2 de noviembre a las 2 PM"),
        ("2026-11-01 20:00", "mañana lunes 2 de noviembre a las 2 PM o el martes 3 de noviembre a las 2 PM"),
    ],
)
def test_calcular_horarios_bordes(instante_mx, esperado):
    with freeze_time(datetime.fromisoformat(instante_mx).replace(tzinfo=MX)):
        assert calcular_horarios(_ahora_mx()) == esperado


@freeze_time("2026-10-30 15:00:00")  # 15:00 UTC = 09:00 MX
def test_calcular_horarios_cantidad_y_rango_configurable():
    ahora = _ahora_mx()
    assert calcular_horarios(ahora, cantidad=1) == "hoy viernes 30 de octubre a las 2 PM"
    assert calcular_horarios(ahora, cantidad=3) == (
        "hoy viernes 30 de octubre a las 2 PM o mañana sábado 31 de octubre a las 2 PM"
        " o el domingo 1 de noviembre a las 2 PM"
    )
    assert calcular_horarios(ahora, hora_inicio=16, hora_fin=18).startswith("hoy viernes 30 de octubre a las 4 PM")
    assert calcular_horarios(ahora, margen_horas=8) == (
        "hoy viernes 30 de octubre a las 5 PM o mañana sábado 31 de octubre a las 2 PM"
    )


def test_calcular_horarios_nunca_ofrece_fuera_de_rango():
    # Barrido de cada media hora durante una semana: toda opción cae entre 2 PM y 7 PM
    validas = {f"{h} PM" for h in range(2, 8)}
    inicio = datetime(2026, 10, 26, tzinfo=MX)
    for paso in range(7 * 48):
        opciones = calcular_horarios(inicio + timedelta(minutes=30 * paso)).split(" o ")
        assert len(opciones) == 2
        for opcion in opciones:
            assert opcion.split(" a las ")[1] in validas


# --- prompt --------------------------------------------------------------
def test_renderizar_sustituye_y_conserva_signo_de_pesos():
    assert renderizar("Cuesta $300. Hoy es {{dia}}", {"dia": "viernes"}) == "Cuesta $300. Hoy es viernes"


def test_renderizar_tolera_llaves_sueltas():
    assert renderizar('JSON {"a": 1} y {{x}}', {"x": "ok"}) == 'JSON {"a": 1} y ok'


def test_renderizar_falla_si_falta_variable():
    with pytest.raises(KeyError):
        renderizar("{{a}} y {{b}}", {"a": 1})


def test_prompt_de_ejemplo_se_renderiza_completo():
    texto = cargar_prompt(datetime(2026, 10, 30, 9, tzinfo=MX))
    assert "{{" not in texto
    assert "hoy viernes 30 de octubre a las 2 PM o mañana sábado 31 de octubre a las 2 PM" in texto
    assert "derivar_a_eduardo" in texto
    assert prompt.ruta_prompt() == prompt.RUTA_EJEMPLO


def test_prompt_real_tiene_prioridad_sobre_el_ejemplo(monkeypatch, tmp_path):
    real = tmp_path / "sofia.md"
    real.write_text("Prompt real {{fecha_hora}}", encoding="utf-8")
    monkeypatch.setattr(prompt, "RUTA_PROMPT", real)
    assert cargar_prompt(datetime(2026, 10, 30, 9, tzinfo=MX)).startswith("Prompt real viernes 30 de octubre de 2026")


def test_sin_prompt_real_avisa_una_sola_vez(monkeypatch, caplog):
    monkeypatch.setattr(prompt, "_avisado", False)
    prompt.ruta_prompt()
    prompt.ruta_prompt()
    assert caplog.text.count("se usa el prompt de ejemplo") == 1


@freeze_time("2026-10-31 02:00:00")  # 02:00 UTC sábado = 20:00 MX viernes
def test_prompt_usa_hora_de_mexico_por_defecto():
    texto = cargar_prompt()
    assert "viernes 30 de octubre de 2026, 08:00 PM" in texto
    assert "mañana sábado 31 de octubre a las 2 PM o el domingo 1 de noviembre a las 2 PM" in texto


# --- historial -----------------------------------------------------------
def test_historial_inicia_con_user():
    filas = [
        {"rol": "assistant", "contenido": "a"},
        {"rol": "user", "contenido": "u"},
        {"rol": "assistant", "contenido": "b"},
    ]
    assert preparar_historial(filas) == [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "b"},
    ]


def test_historial_solo_assistant_queda_vacio():
    assert preparar_historial([{"rol": "assistant", "contenido": "a"}]) == []


# --- whatsapp: firma y parseo -------------------------------------------
@pytest.mark.parametrize(
    "encabezado, secreto, valido",
    [
        (None, "s", False),
        ("", "s", False),
        ("md5=abc", "s", False),
        ("sha256=abc", "", False),
    ],
)
def test_firma_valida_casos_invalidos(encabezado, secreto, valido, monkeypatch):
    monkeypatch.setattr(config, "APP_SECRET", "")
    assert firma_valida(b"{}", encabezado, secreto or None) is valido


def test_firma_valida_detecta_cuerpo_alterado():
    import hashlib
    import hmac

    firma = "sha256=" + hmac.new(b"s", b'{"a":1}', hashlib.sha256).hexdigest()
    assert firma_valida(b'{"a":1}', firma, "s") is True
    assert firma_valida(b'{"a":2}', firma, "s") is False


@pytest.mark.parametrize("body", [{}, {"entry": []}, {"entry": [{"changes": []}]}, None, []])
def test_extraer_mensaje_body_malformado(body):
    assert extraer_mensaje(body) is None


def _body(mensaje):
    return {"entry": [{"changes": [{"value": {"messages": [mensaje]}}]}]}


def test_extraer_mensaje_imagen_con_leyenda():
    body = _body({"from": "1", "id": "w", "type": "image", "image": {"id": "MEDIA1", "caption": "ya pagué"}})
    assert extraer_mensaje(body) == {
        "from": "1",
        "id": "w",
        "type": "image",
        "text": None,
        "media_id": "MEDIA1",
        "caption": "ya pagué",
    }


def test_extraer_mensaje_documento_sin_leyenda():
    mensaje = extraer_mensaje(_body({"from": "1", "id": "w", "type": "document", "document": {"id": "M2"}}))
    assert mensaje["media_id"] == "M2" and mensaje["caption"] is None


def test_extraer_mensaje_nota_de_voz():
    mensaje = extraer_mensaje(_body({"from": "1", "id": "w", "type": "audio", "audio": {"id": "A1", "voice": True}}))
    assert mensaje["type"] == "audio" and mensaje["media_id"] == "A1"


def test_extraer_mensaje_sticker_no_trae_media():
    mensaje = extraer_mensaje(_body({"from": "1", "id": "w", "type": "sticker", "sticker": {"id": "S1"}}))
    assert mensaje["media_id"] is None
