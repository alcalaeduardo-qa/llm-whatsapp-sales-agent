"""Corre los evals de Sofía y escribe evals/RESULTS.md.

    python -m evals.run                    # todo: 48 ventas + 16 red-team, con juez (aprox. 1.5 USD)
    python -m evals.run --sin-juez         # solo checks deterministas (centavos)
    python -m evals.run --solo cierre      # una categoría o un id
    python -m evals.run --limite 5         # los primeros N casos (prueba rápida)

Las transcripciones completas quedan en evals/resultados/ (fuera de git); RESULTS.md e historial.json
solo guardan porcentajes y fallas resumidas.
"""

import os

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")  # nada sale a la nube de DeepEval

import argparse
import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

from app import config, prompt  # noqa: E402
from evals import metricas  # noqa: E402
from evals.simulador import Conversacion, conversar, preparar_entorno  # noqa: E402

DIR_EVALS = RAIZ / "evals"
DIR_RESULTADOS = DIR_EVALS / "resultados"
HISTORIAL = DIR_EVALS / "historial.json"
REPORTE = DIR_EVALS / "RESULTS.md"
MODELO_JUEZ = "claude-opus-5-5"
FASES = ("filtro", "patron", "propuesta", "cierre", "objecion", "derivacion")
CONCURRENCIA = 4
# Precio de claude-haiku-5-5 por token (prompts de menos de 100K tokens)
PRECIO_ENTRADA, PRECIO_SALIDA = 0.10 / 1e6, 0.50 / 1e6


class ContadorTokens(logging.Handler):
    """Suma los tokens que registra app.llm en cada respuesta de Sofía."""

    def __init__(self):
        super().__init__()
        self.entrada = self.salida = 0

    def emit(self, record):
        if record.msg.startswith("Claude uso tokens"):
            self.entrada += int(record.args[0])
            self.salida += int(record.args[1])


def cargar_dataset(solo: str | None, limite: int | None) -> list[dict]:
    casos = []
    for archivo in sorted((DIR_EVALS / "dataset").glob("*.yaml")):
        for caso in yaml.safe_load(archivo.read_text(encoding="utf-8")):
            caso["origen"] = archivo.stem
            casos.append(caso)
    if solo:
        casos = [c for c in casos if solo in (c["id"], c["categoria"], c["origen"])]
    return casos[:limite] if limite else casos


async def calificar(c: Conversacion, texto_prompt: str, juez) -> dict:
    caso = metricas.caso_de_prueba(c, texto_prompt)
    por_escenario = metricas.chequeos_de_escenario(texto_prompt)
    resultados = {}
    for nombre, chequeo in [
        *metricas.GLOBALES.items(),
        *((n, por_escenario[n]) for n in c.escenario.get("espera", [])),
    ]:
        m = metricas.MetricaDeterminista(nombre, chequeo, c)
        await m.a_measure(caso)
        resultados[nombre] = {"score": m.score, "paso": m.success, "razon": m.reason, "tipo": "determinista"}
    if juez is not None and c.escenario["categoria"] in FASES and c.error is None:
        for m in metricas.jueces(juez):
            try:
                await m.a_measure(caso)
                resultados[m.name] = {
                    "score": m.score,
                    "paso": bool(m.success),
                    "razon": m.reason,
                    "tipo": "juez",
                    "costo": m.evaluation_cost or 0,
                }
            except Exception as e:
                resultados[m.name] = {
                    "score": 0.0,
                    "paso": False,
                    "razon": f"juez falló: {e}",
                    "tipo": "juez",
                    "costo": 0,
                }
    return resultados


async def correr(casos: list[dict], con_juez: bool) -> tuple[list[dict], str, str]:
    preparar_entorno()
    ruta = prompt.ruta_prompt()
    texto_prompt = ruta.read_text(encoding="utf-8")
    juez = None
    if con_juez:
        from deepeval.models import AnthropicModel

        juez = AnthropicModel(model=MODELO_JUEZ, generation_kwargs={"output_config": {"effort": "low"}})
    semaforo = asyncio.Semaphore(CONCURRENCIA)

    async def uno(escenario: dict) -> dict:
        async with semaforo:
            c = await conversar(escenario)
            calificacion = await calificar(c, texto_prompt, juez)
            marca = "✓" if all(r["paso"] for r in calificacion.values()) else "✗"
            print(f"  {marca} {escenario['id']}", flush=True)
            return {
                "id": escenario["id"],
                "categoria": escenario["categoria"],
                "origen": escenario["origen"],
                "pais": escenario.get("pais"),
                "turnos": c.turnos,
                "derivacion": c.derivacion,
                "crisis": c.crisis,
                "metricas": calificacion,
            }

    resultados = await asyncio.gather(*(uno(e) for e in casos))
    version = hashlib.sha256(texto_prompt.encode("utf-8")).hexdigest()[:8]
    return list(resultados), version, ruta.name


def resumir(resultados: list[dict], version: str, archivo_prompt: str, segundos: float, tokens: ContadorTokens) -> dict:
    costo_juez = sum(r.get("costo", 0) for res in resultados for r in res["metricas"].values())
    costo_sofia = tokens.entrada * PRECIO_ENTRADA + tokens.salida * PRECIO_SALIDA
    pasan = [all(r["paso"] for r in res["metricas"].values()) for res in resultados]
    return {
        "fecha": datetime.now(config.ZONA_MX).strftime("%Y-%m-%d %H:%M"),
        "prompt": f"{archivo_prompt}@{version}",
        "modelo": config.CLAUDE_MODEL,
        "juez": MODELO_JUEZ
        if any(r["tipo"] == "juez" for res in resultados for r in res["metricas"].values())
        else "—",
        "casos": len(resultados),
        "pasan": sum(pasan),
        "porcentaje": round(100 * sum(pasan) / len(resultados), 1) if resultados else 0.0,
        "costo_usd": round(costo_juez + costo_sofia, 3),
        "segundos": round(segundos),
    }


def _tabla(filas: list[list], encabezado: list[str]) -> str:
    lineas = ["| " + " | ".join(encabezado) + " |", "|" + "|".join("---" for _ in encabezado) + "|"]
    lineas += ["| " + " | ".join(str(x) for x in fila) + " |" for fila in filas]
    return "\n".join(lineas)


def _pct(n: int, total: int) -> str:
    return f"{100 * n / total:.0f} % ({n}/{total})" if total else "—"


def escribir_reporte(resultados: list[dict], resumen: dict, historial: list[dict]) -> None:
    categorias: dict[str, list[dict]] = {}
    for r in resultados:
        categorias.setdefault(r["categoria"], []).append(r)
    filas_categoria = [
        [cat, r[0]["origen"], len(r), _pct(sum(all(m["paso"] for m in x["metricas"].values()) for x in r), len(r))]
        for cat, r in categorias.items()
    ]

    por_metrica: dict[str, list[dict]] = {}
    for r in resultados:
        for nombre, m in r["metricas"].items():
            por_metrica.setdefault(nombre, []).append(m)
    filas_metrica = []
    for nombre, valores in sorted(por_metrica.items(), key=lambda kv: (kv[1][0]["tipo"], kv[0])):
        promedio = sum(v["score"] or 0 for v in valores) / len(valores)
        filas_metrica.append(
            [nombre, valores[0]["tipo"], _pct(sum(v["paso"] for v in valores), len(valores)), f"{promedio:.2f}"]
        )

    fallas = [
        f"- **{r['id']}** · `{nombre}`: {(m['razon'] or '').strip().replace(chr(10), ' ')[:220]}"
        for r in resultados
        for nombre, m in r["metricas"].items()
        if not m["paso"]
    ]
    filas_historial = [
        [h["fecha"], h["prompt"], h["juez"], h["casos"], f"{h['porcentaje']} %", f"{h['costo_usd']} USD"]
        for h in reversed(historial[-15:])
    ]

    REPORTE.write_text(
        "\n\n".join(
            [
                "# Resultados de los evals de Sofía",
                "> Generado por `python -m evals.run`. Un caso **pasa** si cumple todas sus métricas: checks "
                "deterministas (reglas exactas) y, en las conversaciones de venta, dos jueces LLM con rúbrica 0-10 "
                "(umbral 6). Las transcripciones completas no se publican.",
                f"**Última corrida:** {resumen['fecha']} · prompt `{resumen['prompt']}` · Sofía `{resumen['modelo']}` · "
                f"juez `{resumen['juez']}` · **{resumen['porcentaje']} % de casos pasan** ({resumen['pasan']}/{resumen['casos']}) · "
                f"{resumen['costo_usd']} USD · {resumen['segundos']} s",
                "## Por categoría\n\n" + _tabla(filas_categoria, ["Categoría", "Dataset", "Casos", "Pasan"]),
                "## Por métrica\n\n" + _tabla(filas_metrica, ["Métrica", "Tipo", "Pasan", "Score promedio"]),
                "## Historial por versión del prompt\n\n"
                + _tabla(filas_historial, ["Fecha", "Prompt", "Juez", "Casos", "Pasan", "Costo"]),
                "## Fallas de la última corrida\n\n" + ("\n".join(fallas) if fallas else "Ninguna."),
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evals de Sofía")
    parser.add_argument("--sin-juez", action="store_true", help="solo checks deterministas")
    parser.add_argument("--solo", help="una categoría, un id o un dataset (ventas, redteam)")
    parser.add_argument("--limite", type=int, help="correr solo los primeros N casos")
    parser.add_argument(
        "--minimo", type=float, help="porcentaje mínimo de casos que pasan; por debajo sale con error (para CI)"
    )
    args = parser.parse_args()

    # app.main configura INFO al importarse; en los evals solo interesan advertencias y el progreso
    logging.getLogger().setLevel(logging.WARNING)
    for ruidoso in ("httpx", "httpx2", "sofia"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    tokens = ContadorTokens()
    llm_log = logging.getLogger("sofia.llm")
    llm_log.addHandler(tokens)
    llm_log.setLevel(logging.INFO)
    llm_log.propagate = False  # cuenta tokens sin imprimir cada respuesta

    casos = cargar_dataset(args.solo, args.limite)
    print(f"Corriendo {len(casos)} casos {'sin' if args.sin_juez else 'con'} juez…")
    inicio = time.time()
    resultados, version, archivo_prompt = asyncio.run(correr(casos, con_juez=not args.sin_juez))
    resumen = resumir(resultados, version, archivo_prompt, time.time() - inicio, tokens)

    DIR_RESULTADOS.mkdir(exist_ok=True)
    marca = datetime.now(config.ZONA_MX).strftime("%Y%m%d-%H%M%S")
    (DIR_RESULTADOS / f"{marca}_{version}.json").write_text(
        json.dumps({"resumen": resumen, "resultados": resultados}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    historial = json.loads(HISTORIAL.read_text(encoding="utf-8")) if HISTORIAL.exists() else []
    historial.append(resumen)
    HISTORIAL.write_text(json.dumps(historial, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    escribir_reporte(resultados, resumen, historial)
    print(
        f"\n{resumen['porcentaje']} % de casos pasan ({resumen['pasan']}/{resumen['casos']}) · {resumen['costo_usd']} USD"
    )
    print(f"Reporte: {REPORTE.relative_to(RAIZ)}")
    if not cumple_minimo(resumen, args.minimo):
        print(f"::error::Solo pasa {resumen['porcentaje']} % de los casos; el mínimo es {args.minimo} %")
        raise SystemExit(1)


def cumple_minimo(resumen: dict, minimo: float | None) -> bool:
    return minimo is None or resumen["porcentaje"] >= minimo


if __name__ == "__main__":
    main()
