"""Día 5: disponibilidad desde el calendario de iCloud y /reservar (solo por orden de Eduardo)."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import httpx
import icalendar
import pytest

from app import calendario, comandos, config, guardrails
from app.scheduling import SIN_HORARIOS, calcular_horarios
from tests.conftest import payload, post_firmado
from tests.falsos import _alertas, _enviados

MX = config.ZONA_MX
TEL = "5214931234567"
SECRETO_TG = "secreto-telegram"


def _mx(dia, hora, minuto=0, mes=11):
    return datetime(2026, mes, dia, hora, minuto, tzinfo=MX)


def evento(inicio, fin=None, transp=None, duracion=None):
    e = icalendar.Event()
    e.add("dtstart", inicio)
    if fin is not None:
        e.add("dtend", fin)
    if duracion is not None:
        e.add("duration", duracion)
    if transp:
        e.add("transp", transp)
    return e


class CalendarioFalso:
    """Lo que usa app.calendario de caldav: search() y add_event()."""

    def __init__(self, eventos=(), error=None):
        self.eventos = list(eventos)
        self.error = error
        self.creados: list[str] = []
        self.busquedas = 0

    def search(self, event, start, end, expand):
        assert event and expand
        self.busquedas += 1
        if self.error:
            raise self.error
        return [SimpleNamespace(icalendar_component=e) for e in self.eventos]

    def add_event(self, ical: str):
        self.creados.append(ical)
        self.eventos += icalendar.Calendar.from_ical(ical).walk("VEVENT")


@pytest.fixture
def cal(monkeypatch):
    falso = CalendarioFalso()
    monkeypatch.setattr(calendario, "_calendario", lambda: falso)
    monkeypatch.setattr(config, "ICLOUD_USUARIO", "eduardo@icloud.com")
    monkeypatch.setattr(config, "ICLOUD_PASSWORD_APP", "abcd-efgh-ijkl-mnop")
    calendario.invalidar_cache()
    yield falso
    calendario.invalidar_cache()


# --- Lectura de eventos -----------------------------------------------------------------
def test_evento_normal_ocupa_su_horario():
    assert calendario.intervalo_ocupado(evento(_mx(2, 14), _mx(2, 15))) == (_mx(2, 14), _mx(2, 15))


def test_evento_marcado_libre_no_bloquea():
    assert calendario.intervalo_ocupado(evento(_mx(2, 14), _mx(2, 15), transp="TRANSPARENT")) is None


def test_evento_de_todo_el_dia_ocupa_el_dia_completo():
    inicio, fin = calendario.intervalo_ocupado(evento(date(2026, 11, 2), date(2026, 11, 3)))
    assert inicio == _mx(2, 0) and fin == _mx(3, 0)


def test_evento_de_todo_el_dia_sin_fin():
    assert calendario.intervalo_ocupado(evento(date(2026, 11, 2)))[1] == _mx(3, 0)


def test_evento_sin_zona_se_toma_como_hora_mexico():
    assert calendario.intervalo_ocupado(evento(datetime(2026, 11, 2, 16), datetime(2026, 11, 2, 17)))[0] == _mx(2, 16)


def test_evento_en_utc_se_convierte():
    utc = datetime(2026, 11, 2, 20, tzinfo=icalendar.vDatetime.from_ical("20261102T200000Z").tzinfo)
    assert calendario.intervalo_ocupado(evento(utc, utc + timedelta(hours=1)))[0] == _mx(2, 14)


def test_evento_con_duracion_o_sin_fin():
    assert calendario.intervalo_ocupado(evento(_mx(2, 14), duracion=timedelta(minutes=90)))[1] == _mx(2, 15, 30)
    assert calendario.intervalo_ocupado(evento(_mx(2, 14)))[1] == _mx(2, 15)


# --- Horarios libres ----------------------------------------------------------------------
def test_salta_horario_ocupado():
    ahora = _mx(2, 9)  # lunes
    assert calcular_horarios(ahora, ocupados=[(_mx(2, 14), _mx(2, 15))]).startswith(
        "hoy lunes 2 de noviembre a las 3 PM"
    )


def test_evento_que_se_cruza_a_medias_tambien_bloquea():
    ocupados = [(_mx(2, 14, 30), _mx(2, 15, 10))]  # pisa la sesión de 2 y la de 3
    assert calcular_horarios(_mx(2, 9), ocupados=ocupados).startswith("hoy lunes 2 de noviembre a las 4 PM")


def test_tarde_ocupada_pasa_al_dia_siguiente():
    ocupados = [(_mx(2, 13), _mx(2, 21))]
    assert calcular_horarios(_mx(2, 9), ocupados=ocupados).startswith("mañana martes 3 de noviembre a las 2 PM")


def test_semana_llena_avisa_que_eduardo_propondra():
    ocupados = [(_mx(1, 0), _mx(10, 0))]
    assert calcular_horarios(_mx(2, 9), ocupados=ocupados) == SIN_HORARIOS


@pytest.mark.anyio
async def test_lee_ocupados_ordenados_y_usa_cache(cal):
    cal.eventos = [
        evento(_mx(3, 16), _mx(3, 17)),
        evento(_mx(2, 14), _mx(2, 15)),
        evento(_mx(2, 18), _mx(2, 19), transp="TRANSPARENT"),
    ]
    primero = await calendario.ocupados(_mx(2, 9))
    assert primero == [(_mx(2, 14), _mx(2, 15)), (_mx(3, 16), _mx(3, 17))]
    await calendario.ocupados(_mx(2, 10))
    assert cal.busquedas == 1  # mismo día, dentro de la caché
    await calendario.ocupados(_mx(2, 10), usar_cache=False)
    assert cal.busquedas == 2


@pytest.mark.anyio
async def test_opciones_usan_el_calendario(cal):
    cal.eventos = [evento(_mx(2, 14), _mx(2, 15))]
    assert (await calendario.opciones_horario(_mx(2, 9))).startswith("hoy lunes 2 de noviembre a las 3 PM")


@pytest.mark.anyio
async def test_sin_icloud_configurado_no_se_consulta(cal, monkeypatch):
    monkeypatch.setattr(config, "ICLOUD_PASSWORD_APP", "")
    cal.eventos = [evento(_mx(2, 14), _mx(2, 15))]
    assert (await calendario.opciones_horario(_mx(2, 9))).startswith("hoy lunes 2 de noviembre a las 2 PM")
    assert cal.busquedas == 0


@pytest.mark.anyio
async def test_icloud_caido_ofrece_horarios_calculados(cal, caplog):
    cal.error = ConnectionError("iCloud no responde")
    assert (await calendario.opciones_horario(_mx(2, 9))).startswith("hoy lunes 2 de noviembre a las 2 PM")
    assert "No se pudo leer el calendario de iCloud" in caplog.text


# --- Crear la reserva -----------------------------------------------------------------------
@pytest.mark.anyio
async def test_reservar_crea_evento_de_una_hora(cal):
    assert await calendario.reservar(_mx(2, 14), TEL) == _mx(2, 15)
    (ical,) = cal.creados
    (creado,) = icalendar.Calendar.from_ical(ical).walk("VEVENT")
    assert creado.decoded("DTSTART") == _mx(2, 14) and creado.decoded("DTEND") == _mx(2, 15)
    assert str(creado["SUMMARY"]) == f"Sesión +{TEL}"
    assert "aprobación de Eduardo" in str(creado["DESCRIPTION"])
    # Alertas para Eduardo: 10 minutos antes y a la hora
    alarmas = creado.walk("VALARM")
    assert [a.decoded("TRIGGER") for a in alarmas] == [timedelta(minutes=-10), timedelta(0)]
    assert all(str(a["ACTION"]) == "DISPLAY" for a in alarmas)


@pytest.mark.anyio
async def test_reservar_sobre_algo_ocupado_no_crea_nada(cal):
    cal.eventos = [evento(_mx(2, 14, 30), _mx(2, 16))]
    with pytest.raises(calendario.HorarioOcupado):
        await calendario.reservar(_mx(2, 14), TEL)
    assert cal.creados == []


@pytest.mark.anyio
async def test_reservar_ignora_la_cache(cal):
    await calendario.ocupados(_mx(2, 0))  # caché vacía del día
    cal.eventos = [evento(_mx(2, 14), _mx(2, 15))]  # alguien agendó desde el iPhone
    with pytest.raises(calendario.HorarioOcupado):
        await calendario.reservar(_mx(2, 14), TEL)


@pytest.mark.anyio
async def test_despues_de_reservar_ya_no_se_ofrece(cal):
    await calendario.opciones_horario(_mx(2, 9))
    await calendario.reservar(_mx(2, 14), TEL)
    assert (await calendario.opciones_horario(_mx(2, 9))).startswith("hoy lunes 2 de noviembre a las 3 PM")


# --- /reservar en Telegram ------------------------------------------------------------------
@pytest.mark.parametrize(
    "dia, hora, esperado",
    [
        ("2/11", "14", _mx(2, 14)),
        ("2/11", "2", _mx(2, 14)),
        ("2/11", "2pm", _mx(2, 14)),
        ("2/11", "2 PM", _mx(2, 14)),
        ("2/11", "16:00", _mx(2, 16)),
        ("hoy", "5", _mx(1, 17)),
        ("mañana", "3pm", _mx(2, 15)),
        ("manana", "15", _mx(2, 15)),
        ("5/1", "14", datetime(2027, 1, 5, 14, tzinfo=MX)),  # ya pasó este año: el siguiente
        ("31/2", "14", None),
        ("lunes", "14", None),
        ("2/11", "10am", None),
        ("2/11", "25", None),
        ("2/11", "tarde", None),
    ],
)
def test_interpretar_fecha_hora(dia, hora, esperado):
    assert comandos.interpretar_fecha_hora(dia, hora, _mx(1, 9)) == esperado


def test_mensaje_de_reserva_cumple_las_reglas():
    texto = guardrails.RESPUESTA_RESERVA.format(cuando="lunes 2 de noviembre a las 2 PM")
    assert guardrails.validar_salida(texto) == []


@pytest.fixture
def telegram_listo(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", SECRETO_TG)


def _orden(entorno, texto, citado=None):
    mensaje = {"message_id": 1, "chat": {"id": 999}, "text": texto}
    if citado:
        mensaje["reply_to_message"] = {"message_id": 2, "caption": citado}
    entorno["http"].post(
        "/telegram/webhook",
        json={"update_id": 1, "message": mensaje},
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRETO_TG},
    )


@pytest.fixture
def manana_2pm():
    """Mañana a las 2 PM en formato de /reservar, para no depender del día en que corren los tests."""
    manana = datetime.now(MX) + timedelta(days=1)
    return f"{manana.day}/{manana.month}", manana.replace(hour=14, minute=0, second=0, microsecond=0)


def test_reservar_crea_evento_y_avisa_a_la_persona(entorno, cal, telegram_listo, manana_2pm):
    dia, inicio = manana_2pm
    _orden(entorno, f"/reservar {TEL} {dia} 14")

    assert len(cal.creados) == 1
    (enviado,) = _enviados(entorno["meta"])
    assert enviado.startswith("Tu pago quedó confirmado") and "a las 2 PM hora México" in enviado
    assert entorno["store"].respuestas_a(TEL) == [enviado]
    (alerta,) = _alertas(entorno["telegram"])
    assert alerta.startswith(f"📅 Reservado en tu calendario Casa: +{TEL}") and "Le avisé por WhatsApp" in alerta
    assert entorno["claude"].peticiones == []  # la IA no participa en la reserva


def test_reservar_contestando_a_la_alerta_del_comprobante(entorno, cal, telegram_listo, manana_2pm):
    dia, _ = manana_2pm
    _orden(entorno, f"/reservar {dia} 2pm", citado=f"🧾 +{TEL} envió una imagen (posible comprobante)")
    assert len(cal.creados) == 1 and entorno["meta"].call_count == 1


def test_reservar_horario_ocupado(entorno, cal, telegram_listo, manana_2pm):
    dia, inicio = manana_2pm
    cal.eventos = [evento(inicio, inicio + timedelta(hours=1))]
    _orden(entorno, f"/reservar {TEL} {dia} 14")
    assert cal.creados == [] and entorno["meta"].call_count == 0
    assert "ya está ocupado" in _alertas(entorno["telegram"])[0]


@pytest.mark.parametrize(
    "orden, error",
    [
        ("/reservar", "Uso /reservar"),
        (f"/reservar {TEL}", "Uso /reservar"),
        (f"/reservar {TEL} lunes 14", "Uso /reservar"),
        ("/reservar 2/11 14", "Uso /reservar"),  # sin teléfono ni alerta citada
        (f"/reservar {TEL} 1/1 14", None),  # 1 de enero del año siguiente: válido
    ],
)
def test_reservar_orden_incompleta(entorno, cal, telegram_listo, orden, error):
    _orden(entorno, orden)
    if error:
        assert error in _alertas(entorno["telegram"])[0] and cal.creados == []


def test_reservar_fuera_de_horario(entorno, cal, telegram_listo, manana_2pm):
    dia, _ = manana_2pm
    _orden(entorno, f"/reservar {TEL} {dia} 20")
    assert "Las sesiones empiezan entre las 2 PM y las 7 PM" in _alertas(entorno["telegram"])[0]
    assert cal.creados == []


@pytest.mark.anyio
async def test_reservar_en_el_pasado(entorno, cal):
    hace_una_hora = (datetime.now(MX) - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    await comandos.reservar(TEL, hace_una_hora)
    assert "ya pasó" in _alertas(entorno["telegram"])[0]
    assert cal.creados == []


def test_reservar_sin_icloud(entorno, cal, telegram_listo, manana_2pm, monkeypatch):
    monkeypatch.setattr(config, "ICLOUD_USUARIO", "")
    _orden(entorno, f"/reservar {TEL} {manana_2pm[0]} 14")
    assert "Falta configurar iCloud" in _alertas(entorno["telegram"])[0]


def test_reservar_con_icloud_caido(entorno, cal, telegram_listo, manana_2pm):
    cal.error = ConnectionError("iCloud no responde")
    _orden(entorno, f"/reservar {TEL} {manana_2pm[0]} 14")
    assert "No pude escribir en tu calendario" in _alertas(entorno["telegram"])[0]
    assert entorno["meta"].call_count == 0


def test_reservar_aunque_falle_whatsapp(entorno, cal, telegram_listo, manana_2pm):
    entorno["meta"].mock(return_value=httpx.Response(400, json={"error": {"code": 131047}}))
    _orden(entorno, f"/reservar {TEL} {manana_2pm[0]} 14")
    assert len(cal.creados) == 1
    assert "No pude avisarle por WhatsApp" in _alertas(entorno["telegram"])[0]


def test_la_ia_recibe_los_horarios_del_calendario(entorno, monkeypatch):
    async def opciones(_ahora):
        return "mañana lunes 2 de noviembre a las 5 PM o el martes 3 de noviembre a las 3 PM"

    monkeypatch.setattr(calendario, "opciones_horario", opciones)
    post_firmado(entorno["http"], payload(texto="¿Qué horarios tienes?"))
    assert "mañana lunes 2 de noviembre a las 5 PM" in entorno["claude"].peticiones[0]["system"]


def test_reservar_fuera_de_ventana_no_intenta_avisar(entorno, cal, telegram_listo, manana_2pm):
    """Caso real: Meta aceptaba el aviso y luego lo marcaba fallido (131047) sin que Eduardo se enterara."""
    entorno["store"].ventana = False
    _orden(entorno, f"/reservar {TEL} {manana_2pm[0]} 14")
    assert len(cal.creados) == 1  # la reserva sí se hace
    assert entorno["meta"].call_count == 0
    assert "No le avisé: su último mensaje fue hace más de 24 horas" in _alertas(entorno["telegram"])[0]
