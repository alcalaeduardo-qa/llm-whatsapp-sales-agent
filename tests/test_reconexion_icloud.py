"""Día 9: iCloud corta la conexión HTTP/3 cada cierto tiempo; se reintenta con una conexión nueva."""

import logging
import sys
from datetime import datetime
from types import SimpleNamespace

import icalendar
import pytest

from app import calendario, config

CALENDARIO_REAL = calendario._calendario  # el conftest lo bloquea durante cada test
INICIO = datetime(2026, 11, 10, 15, tzinfo=config.ZONA_MX)
FIN = datetime(2026, 11, 10, 16, tzinfo=config.ZONA_MX)


class Corte(Exception):
    pass


class CalendarioFalso:
    def __init__(self, cortes=0):
        self.cortes = cortes
        self.busquedas = 0
        self.eventos_creados: list[str] = []

    def _quiza_cortar(self):
        if self.cortes:
            self.cortes -= 1
            raise Corte("HTTP/3 connection terminated")

    def search(self, **_filtros):
        self.busquedas += 1
        self._quiza_cortar()
        evento = icalendar.Event()
        evento.add("uid", "uid-1")
        evento.add("summary", "Sesión +5214931234567")
        evento.add("dtstart", INICIO)
        evento.add("dtend", FIN)
        return [SimpleNamespace(icalendar_component=evento)]

    def add_event(self, ics):
        self.eventos_creados.append(ics)
        self._quiza_cortar()


@pytest.fixture
def falso(monkeypatch):
    cal = CalendarioFalso()
    monkeypatch.setattr(calendario, "_calendario", lambda: cal)
    return cal


def test_un_corte_no_se_nota_al_leer_sesiones(falso, caplog):
    falso.cortes = 1

    with caplog.at_level(logging.WARNING, logger="sofia.calendario"):
        sesiones = calendario._leer_sesiones(INICIO, FIN)

    assert sesiones == [("uid-1", INICIO, "5214931234567")]
    assert falso.busquedas == 2
    assert "iCloud falló (Corte)" in caplog.text


def test_un_corte_no_se_nota_al_leer_ocupados(falso):
    falso.cortes = 1

    assert calendario._leer_ocupados(INICIO, FIN) == [(INICIO, FIN)]


def test_sin_cortes_no_se_reintenta(falso):
    calendario._leer_sesiones(INICIO, FIN)

    assert falso.busquedas == 1


def test_dos_cortes_seguidos_llegan_a_quien_llamo(falso):
    falso.cortes = 2

    with pytest.raises(Corte):
        calendario._leer_sesiones(INICIO, FIN)

    assert falso.busquedas == 2


def test_el_reintento_de_una_reserva_usa_el_mismo_evento(falso):
    falso.cortes = 1

    calendario._crear_evento(INICIO, FIN, "5214931234567")

    primero, segundo = falso.eventos_creados
    assert primero == segundo  # mismo UID: iCloud no puede guardar dos sesiones


def test_el_reintento_abre_una_conexion_nueva(monkeypatch):
    conexiones = []

    class Cliente:
        def __init__(self, **_datos):
            conexiones.append(self)
            self.cal = CalendarioFalso(cortes=1 if len(conexiones) == 1 else 0)

        def principal(self):
            return SimpleNamespace(calendar=lambda name: self.cal)

    monkeypatch.setitem(sys.modules, "caldav", SimpleNamespace(DAVClient=Cliente))
    monkeypatch.setattr(calendario, "_calendario", CALENDARIO_REAL)
    calendario._abrir_calendario.cache_clear()

    calendario._leer_sesiones(INICIO, FIN)
    calendario._leer_sesiones(INICIO, FIN)

    assert len(conexiones) == 2  # la segunda se conserva para las siguientes consultas
    calendario._abrir_calendario.cache_clear()
