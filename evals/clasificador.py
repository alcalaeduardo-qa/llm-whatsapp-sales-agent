"""Mide el clasificador de crisis contra casos etiquetados: sensibilidad (crisis reales detectadas)
y especificidad (ventas que no se cortan sin motivo).

    python -m evals.clasificador          # cada caso 3 veces (el modelo no es determinista)
"""

import asyncio
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

from app import llm  # noqa: E402

REPETICIONES = 3


async def medir() -> dict:
    casos = yaml.safe_load((RAIZ / "evals" / "casos_crisis.yaml").read_text(encoding="utf-8"))
    resultados = {"si": [], "no": []}
    for etiqueta, lista in (("si", casos["si"]), ("no", casos["no"])):
        for mensajes in lista:
            votos = [await llm.clasificar_crisis(mensajes) for _ in range(REPETICIONES)]
            resultados[etiqueta].append((mensajes[-1], sum(votos)))
    return resultados


def main() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    r = asyncio.run(medir())
    detectadas = sum(v for _, v in r["si"])
    falsas = sum(v for _, v in r["no"])
    total_si, total_no = len(r["si"]) * REPETICIONES, len(r["no"]) * REPETICIONES
    print(f"Sensibilidad: {detectadas}/{total_si} crisis reales detectadas")
    print(f"Especificidad: {total_no - falsas}/{total_no} casos sin riesgo bien descartados")
    for texto, votos in r["si"]:
        if votos < REPETICIONES:
            print(f"  ✗ crisis no detectada ({votos}/{REPETICIONES}): {texto[:80]}")
    for texto, votos in r["no"]:
        if votos:
            print(f"  ✗ falsa crisis ({votos}/{REPETICIONES}): {texto[:80]}")


if __name__ == "__main__":
    main()
