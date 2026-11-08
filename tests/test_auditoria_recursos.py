"""Auditoría, pistas 19, 20 y 21: archivos sin tope de tamaño, una lectura de iCloud por mensaje y evals del CI que
exponían la clave a cualquier PR."""

from datetime import datetime
from pathlib import Path

import httpx
import pytest
import yaml

from app import calendario, config, whatsapp

URL_INFO = f"https://graph.facebook.com/{config.GRAPH_API_VERSION}/m1"
URL_ARCHIVO = "https://lookaside.example/archivo"


@pytest.fixture
def media(respx_mock):
    def preparar(contenido: bytes, file_size=None):
        info = {"url": URL_ARCHIVO, "mime_type": "image/jpeg"}
        if file_size is not None:
            info["file_size"] = file_size
        respx_mock.get(URL_INFO).mock(return_value=httpx.Response(200, json=info))
        return respx_mock.get(URL_ARCHIVO).mock(return_value=httpx.Response(200, content=contenido))

    return preparar


@pytest.mark.anyio
async def test_un_archivo_normal_se_descarga(media):
    media(b"x" * 1000)

    assert await whatsapp.descargar_media("m1") == (b"x" * 1000, "image/jpeg")


@pytest.mark.anyio
async def test_un_archivo_enorme_se_corta_al_descargar(media, monkeypatch):
    monkeypatch.setattr(whatsapp, "MAX_MEDIA_BYTES", 100)
    media(b"x" * 1000)

    with pytest.raises(whatsapp.ArchivoMuyGrande):
        await whatsapp.descargar_media("m1")


@pytest.mark.anyio
async def test_si_meta_declara_un_archivo_enorme_ni_se_descarga(media, monkeypatch):
    monkeypatch.setattr(whatsapp, "MAX_MEDIA_BYTES", 100)
    ruta = media(b"x", file_size=10_000)

    with pytest.raises(whatsapp.ArchivoMuyGrande):
        await whatsapp.descargar_media("m1")
    assert not ruta.called


@pytest.mark.anyio
async def test_las_sesiones_de_una_persona_se_leen_una_vez_por_ventana(monkeypatch):
    lecturas = []
    inicio = datetime(2030, 1, 3, 15, tzinfo=config.ZONA_MX)

    async def sesiones_entre(desde, hasta):
        lecturas.append(desde)
        return [("uid", inicio, "521")]

    monkeypatch.setattr(calendario, "configurado", lambda: True)
    monkeypatch.setattr(calendario, "sesiones_entre", sesiones_entre)
    calendario.invalidar_cache()
    ahora = datetime(2030, 1, 2, 10, tzinfo=config.ZONA_MX)

    resultados = [await calendario.sesiones_de("521", ahora) for _ in range(5)]

    assert len(lecturas) == 1 and resultados[0] == [inicio]
    calendario.invalidar_cache()
    await calendario.sesiones_de("521", ahora)
    assert len(lecturas) == 2


@pytest.mark.anyio
async def test_icloud_corre_en_sus_propios_hilos():
    nombre = await calendario._en_hilo(lambda: __import__("threading").current_thread().name)

    assert nombre.startswith("icloud")


def _flujo(nombre):
    return yaml.safe_load(Path(".github/workflows", nombre).read_text(encoding="utf-8"))


def test_los_evals_del_ci_solo_corren_con_codigo_del_duenio():
    condicion = _flujo("ci.yml")["jobs"]["evals"]["if"]

    assert "repository_owner" in condicion and "push" in condicion


def test_los_evals_con_juez_solo_los_lanza_el_duenio():
    assert "repository_owner" in _flujo("evals-juez.yml")["jobs"]["evals"]["if"]


def test_ningun_flujo_publica_el_reporte_de_evals():
    for nombre in ("ci.yml", "evals-juez.yml"):
        assert "upload-artifact" not in Path(".github/workflows", nombre).read_text(encoding="utf-8")


def test_el_semaforo_limita_las_descargas_a_la_vez():
    assert whatsapp._DESCARGAS._value == 3
