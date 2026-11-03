"""Día 9: BORRAR MIS DATOS (derecho de cancelación). La retención de 90 días vive en SQL (db/schema.sql)."""

from types import SimpleNamespace

import pytest

from app import guardrails, store
from tests.conftest import payload, post_firmado
from tests.falsos import _alertas, _enviados

TEL = "5214931234567"


@pytest.mark.parametrize(
    "texto",
    [
        "BORRAR MIS DATOS",
        "borrar mis datos",
        "Borrar mis datos.",
        "  Borra mis datos!! ",
        "eliminar mi información",
        "Elimina mi historial",
        "[nota de voz] Borrar mis datos.",  # dicho en una nota de voz
    ],
)
def test_reconoce_la_peticion(texto):
    assert guardrails.es_borrar_mis_datos(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "¿Cómo borro mis datos de pago?",
        "No quiero borrar mis datos todavía",
        "borrar",
        "Mis datos son de México",
        "Quiero borrar a mi ex de mi vida",
    ],
)
def test_no_borra_por_accidente(texto):
    assert not guardrails.es_borrar_mis_datos(texto)


def test_borra_todo_confirma_y_avisa(entorno):
    post_firmado(entorno["http"], payload(texto="Hola", wa_id="w1"))
    entorno["store"].humano[TEL] = "comprobante"
    entorno["store"].pausadas[TEL] = "crisis"
    entorno["store"].filas.append({"telefono": "573001234567", "rol": "user", "contenido": "otra persona"})

    post_firmado(entorno["http"], payload(texto="BORRAR MIS DATOS", wa_id="w2"))

    assert [f for f in entorno["store"].filas if f["telefono"] == TEL] == []  # ni siquiera el comando
    assert entorno["store"].filas == [{"telefono": "573001234567", "rol": "user", "contenido": "otra persona"}]
    assert entorno["store"].humano == {} and entorno["store"].pausadas == {}
    assert _enviados(entorno["meta"])[-1] == guardrails.RESPUESTA_DATOS_BORRADOS
    alerta = _alertas(entorno["telegram"])[-1]
    assert alerta.startswith(f"🗑️ +{TEL} pidió BORRAR MIS DATOS") and "2 mensajes" in alerta
    assert len(entorno["claude"].peticiones) == 1  # solo la respuesta al "Hola"


def test_si_falla_el_borrado_avisa_a_los_dos(entorno, monkeypatch):
    async def falla(_telefono):
        raise RuntimeError("Supabase caído")

    monkeypatch.setattr(store, "borrar_datos", falla)
    post_firmado(entorno["http"], payload(texto="borrar mis datos"))
    assert _enviados(entorno["meta"]) == [guardrails.RESPUESTA_DATOS_NO_BORRADOS]
    assert "falló el borrado" in _alertas(entorno["telegram"])[0]


def test_borrado_avisa_a_eduardo_aunque_falle_whatsapp(entorno):
    import httpx

    entorno["meta"].mock(return_value=httpx.Response(500))
    post_firmado(entorno["http"], payload(texto="borrar mis datos"))
    assert "BORRAR MIS DATOS" in _alertas(entorno["telegram"])[0]


@pytest.mark.parametrize("respuesta", [guardrails.RESPUESTA_DATOS_BORRADOS, guardrails.RESPUESTA_DATOS_NO_BORRADOS])
def test_respuestas_cumplen_las_reglas(respuesta):
    assert guardrails.validar_salida(respuesta) == []


# --- store.borrar_datos contra un Supabase falso -------------------------------------------------
class SupabaseBorrado:
    def __init__(self):
        self.borrados: list[tuple[str, str]] = []

    def table(self, nombre):
        self._tabla = nombre
        return self

    def delete(self):
        return self

    def eq(self, columna, valor):
        assert columna == "telefono"
        self._valor = valor
        return self

    def execute(self):
        self.borrados.append((self._tabla, self._valor))
        filas = [{"id": 1}, {"id": 2}, {"id": 3}] if self._tabla == "mensajes" else [{"id": 1}]
        return SimpleNamespace(data=filas)


@pytest.mark.anyio
async def test_store_borra_las_tres_tablas(monkeypatch):
    db = SupabaseBorrado()
    monkeypatch.setattr(store, "_cliente", lambda: db)
    assert await store.borrar_datos("521") == 3
    assert db.borrados == [("mensajes", "521"), ("conversaciones", "521"), ("recordatorios", "521")]
