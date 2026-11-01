"""app.store contra un cliente de Supabase falso (misma interfaz encadenable)."""

from types import SimpleNamespace

import pytest

from app import store


class ConsultaFalsa:
    def __init__(self, db, tabla):
        self.db, self.tabla = db, tabla
        self.fila = None
        self.filtros = {}
        self.descendente = False
        self.limite = None

    def insert(self, fila):
        self.fila = fila
        return self

    def select(self, _columnas):
        return self

    def eq(self, columna, valor):
        self.filtros[columna] = valor
        return self

    def order(self, _columna, desc=False):
        self.descendente = desc
        return self

    def limit(self, n):
        self.limite = n
        return self

    def execute(self):
        if self.fila is not None:
            return self.db.insertar(self.fila)
        filas = [f for f in self.db.filas if all(f.get(k) == v for k, v in self.filtros.items())]
        if self.descendente:
            filas = list(reversed(filas))
        return SimpleNamespace(data=filas[: self.limite])


class SupabaseFalso:
    def __init__(self, error=None):
        self.filas: list[dict] = []
        self.error = error

    def table(self, nombre):
        assert nombre == "mensajes"
        return ConsultaFalsa(self, nombre)

    def insertar(self, fila):
        if self.error:
            raise self.error
        wa_id = fila.get("wa_message_id")
        if wa_id and any(f.get("wa_message_id") == wa_id for f in self.filas):
            raise Exception("{'code': '23505', 'message': 'duplicate key value violates unique constraint'}")
        self.filas.append(fila)
        return SimpleNamespace(data=[fila])


@pytest.fixture
def db(monkeypatch):
    falso = SupabaseFalso()
    monkeypatch.setattr(store, "_cliente", lambda: falso)
    return falso


@pytest.mark.anyio
async def test_registrar_entrante_y_duplicado(db):
    assert await store.registrar_entrante("521", "hola", "wamid.1") is True
    assert await store.registrar_entrante("521", "hola", "wamid.1") is False
    assert len(db.filas) == 1


@pytest.mark.anyio
async def test_error_distinto_a_duplicado_se_propaga(monkeypatch):
    monkeypatch.setattr(store, "_cliente", lambda: SupabaseFalso(error=RuntimeError("conexión rechazada")))
    with pytest.raises(RuntimeError):
        await store.registrar_entrante("521", "hola", "wamid.1")


@pytest.mark.anyio
async def test_historial_cronologico_y_limitado(db):
    await store.registrar_entrante("521", "u1", "w1")
    await store.guardar_respuesta("521", "a1")
    await store.registrar_entrante("521", "u2", "w2")
    await store.guardar_respuesta("521", "a2")
    await store.registrar_entrante("999", "otro", "w3")

    # Los 3 más recientes: a1, u2, a2 → se recorta el assistant inicial
    assert await store.obtener_historial("521", limite=3) == [
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    assert len(await store.obtener_historial("521")) == 4


@pytest.mark.parametrize(
    "error, esperado",
    [
        (Exception("{'code': '23505'}"), True),
        (Exception("Duplicate key value"), True),
        (Exception("timeout"), False),
    ],
)
def test_es_duplicado(error, esperado):
    assert store._es_duplicado(error) is esperado
