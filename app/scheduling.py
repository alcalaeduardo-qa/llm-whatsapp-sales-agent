from datetime import datetime, time, timedelta

from app import config

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _etiqueta_dia(dias_extra: int, fecha) -> str:
    if dias_extra == 0:
        return "hoy"
    if dias_extra == 1:
        return "mañana"
    return "el " + DIAS[fecha.weekday()]


def calcular_horarios(
    ahora: datetime,
    hora_inicio: int = config.HORA_INICIO,
    hora_fin: int = config.HORA_FIN,
    margen_horas: int = config.MARGEN_HORAS,
    cantidad: int = 2,
) -> str:
    """Primer horario libre de cada día, respetando el margen mínimo.

    Devuelve hasta `cantidad` opciones en días distintos, p. ej.
    "hoy a las 5 PM o mañana a las 2 PM".
    """
    zona = ahora.tzinfo
    limite = ahora + timedelta(hours=margen_horas)
    opciones = []
    for dias_extra in range(7):
        fecha = (ahora + timedelta(days=dias_extra)).date()
        for hora in range(hora_inicio, hora_fin):
            inicio = datetime.combine(fecha, time(hora), tzinfo=zona)
            if inicio >= limite:
                hora_texto = inicio.strftime("%I").lstrip("0")
                opciones.append(f"{_etiqueta_dia(dias_extra, fecha)} a las {hora_texto} PM")
                break
        if len(opciones) == cantidad:
            break
    return " o ".join(opciones)
