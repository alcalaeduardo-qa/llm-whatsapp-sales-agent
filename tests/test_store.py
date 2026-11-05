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


# --- conversaciones / pausa de venta ---------------------------------------
class SupabaseConversaciones:
    """Solo la tabla conversaciones: upsert por teléfono y select con eq."""

    def __init__(self, error=None):
        self.filas: dict[str, dict] = {}
        self.error = error
        self._filtro = None
        self._fila = None

    def table(self, nombre):
        assert nombre == "conversaciones"
        self._filtro = self._fila = None
        return self

    def upsert(self, fila):
        self._fila = fila
        return self

    def select(self, _columnas):
        return self

    def eq(self, _columna, valor):
        self._filtro = valor
        return self

    def limit(self, _n):
        return self

    def execute(self):
        if self.error:
            raise self.error
        if self._fila:
            # Como PostgREST: el upsert solo cambia las columnas enviadas
            telefono = self._fila["telefono"]
            self.filas[telefono] = {**self.filas.get(telefono, {}), **self._fila}
            return SimpleNamespace(data=[self.filas[telefono]])
        fila = self.filas.get(self._filtro)
        return SimpleNamespace(data=[fila] if fila else [])


@pytest.fixture
def conversaciones(monkeypatch):
    db = SupabaseConversaciones()
    monkeypatch.setattr(store, "_cliente", lambda: db)
    return db


@pytest.mark.anyio
async def test_guardar_metrica_inserta_la_fila_en_su_tabla(monkeypatch):
    insertadas = []

    class Metricas:
        def table(self, nombre):
            assert nombre == "metricas"
            return self

        def insert(self, fila):
            insertadas.append(fila)
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    monkeypatch.setattr(store, "_cliente", Metricas)

    await store.guardar_metrica({"fase": "crisis"})

    assert insertadas == [{"fase": "crisis"}]


@pytest.mark.anyio
async def test_estado_inicial_sin_fila(conversaciones):
    assert await store.obtener_estado("521") == store.ESTADO_INICIAL


@pytest.mark.anyio
async def test_pausar_venta(conversaciones):
    assert await store.pausar_venta("521", "crisis") is True
    assert (await store.obtener_estado("521"))["venta_pausada"] is True
    fila = conversaciones.filas["521"]
    assert fila["motivo_pausa"] == "crisis"
    assert fila["pausada_en"].endswith("+00:00") and fila["actualizado_en"].endswith("+00:00")


@pytest.mark.anyio
async def test_modo_humano_y_reanudar(conversaciones):
    await store.pausar_venta("521", "crisis")
    await store.activar_humano("521", "comprobante")
    assert await store.marcar_aviso_privacidad("521") is True
    assert await store.obtener_estado("521") == dict.fromkeys(store.ESTADO_INICIAL, True)
    assert conversaciones.filas["521"]["motivo_humano"] == "comprobante"

    assert await store.reanudar("521") is True
    # Vuelve a Sofía, pero se recuerda que Eduardo ya la atendió
    assert await store.obtener_estado("521") == {
        "venta_pausada": False,
        "humano_activo": False,
        "atendida_por_eduardo": True,
        "aviso_privacidad": True,
    }
    assert conversaciones.filas["521"]["motivo_pausa"] == "crisis"  # se conserva el historial del motivo


@pytest.mark.anyio
async def test_estado_tolera_fallas_de_la_base(monkeypatch, caplog):
    monkeypatch.setattr(store, "_cliente", lambda: SupabaseConversaciones(error=RuntimeError("no existe la tabla")))
    # No se asume que la conversación está libre: se marca como desconocido para no responder con el modelo
    assert await store.obtener_estado("521") == {**store.ESTADO_INICIAL, "desconocido": True}
    assert await store.pausar_venta("521", "crisis") is False  # no lanza
    assert await store.activar_humano("521", "comprobante") is False
    assert await store.reanudar("521") is False
    assert "No se pudo pausar la venta" in caplog.text


# --- recordatorios ---------------------------------------------------------------------
class SupabaseRecordatorios:
    def __init__(self, error=None):
        self.filas: list[dict] = []
        self.error = error

    def table(self, nombre):
        assert nombre == "recordatorios"
        return self

    def insert(self, fila):
        self._fila = fila
        return self

    def execute(self):
        if self.error:
            raise self.error
        if any(f["evento_uid"] == self._fila["evento_uid"] for f in self.filas):
            raise Exception("{'code': '23505', 'message': 'duplicate key'}")
        self.filas.append(self._fila)
        return SimpleNamespace(data=[self._fila])


@pytest.mark.anyio
async def test_marcar_recordatorio_una_sola_vez(monkeypatch):
    from datetime import datetime

    from app import config

    db = SupabaseRecordatorios()
    monkeypatch.setattr(store, "_cliente", lambda: db)
    inicio = datetime(2026, 11, 2, 14, tzinfo=config.ZONA_MX)
    assert await store.marcar_recordatorio("uid-1", "521", inicio) is True
    assert await store.marcar_recordatorio("uid-1", "521", inicio) is False
    assert db.filas == [{"evento_uid": "uid-1", "telefono": "521", "inicio": "2026-11-02T14:00:00-06:00"}]


@pytest.mark.anyio
async def test_marcar_recordatorio_propaga_otros_errores(monkeypatch):
    from datetime import datetime

    monkeypatch.setattr(store, "_cliente", lambda: SupabaseRecordatorios(error=RuntimeError("caído")))
    with pytest.raises(RuntimeError):
        await store.marcar_recordatorio("uid-1", "521", datetime(2026, 11, 2, 14))


# --- ventana de 24 h -----------------------------------------------------------------------
class SupabaseUltimo:
    def __init__(self, filas=None, error=None):
        self.filas, self.error = filas or [], error

    def table(self, _nombre):
        return self

    def select(self, _c):
        return self

    def eq(self, _c, _v):
        return self

    def order(self, _c, desc=False):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.filas)


@pytest.mark.anyio
async def test_ventana_de_whatsapp(monkeypatch):
    from datetime import UTC, datetime, timedelta

    reciente = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    viejo = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    monkeypatch.setattr(store, "_cliente", lambda: SupabaseUltimo([{"creado_en": reciente}]))
    assert await store.en_ventana_de_whatsapp("521") is True
    monkeypatch.setattr(store, "_cliente", lambda: SupabaseUltimo([{"creado_en": viejo}]))
    assert await store.en_ventana_de_whatsapp("521") is False
    monkeypatch.setattr(store, "_cliente", lambda: SupabaseUltimo([]))
    assert await store.en_ventana_de_whatsapp("521") is False  # nunca escribió
    monkeypatch.setattr(store, "_cliente", lambda: SupabaseUltimo(error=RuntimeError("caído")))
    assert await store.en_ventana_de_whatsapp("521") is None
