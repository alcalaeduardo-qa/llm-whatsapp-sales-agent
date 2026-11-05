"""Día 9: los logs de producción no guardan secretos ni ruido."""

import logging
from pathlib import Path

from app import main  # noqa: F401  (configura los loggers al importarse)


def test_produccion_arranca_sin_log_de_acceso():
    # Meta manda VERIFY_TOKEN en la URL al verificar el webhook
    assert "--no-access-log" in Path("Dockerfile").read_text(encoding="utf-8")


def test_los_avisos_de_quic_no_llegan_al_log():
    assert logging.getLogger("quic").getEffectiveLevel() == logging.ERROR
