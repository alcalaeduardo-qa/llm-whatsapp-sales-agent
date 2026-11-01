from datetime import date, datetime, time, timedelta

from app import config

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]


def dia_y_fecha(fecha: date) -> str:
    """ "lunes 2 de noviembre", en español sin depender del idioma del sistema operativo."""
    return f"{DIAS[fecha.weekday()]} {fecha.day} de {MESES[fecha.month - 1]}"


def fecha_hora_legible(ahora: datetime) -> str:
    """ "domingo 1 de noviembre de 2026, 12:57 AM" para el prompt."""
    return f"{dia_y_fecha(ahora.date())} de {ahora.year}, {ahora.strftime('%I:%M %p')}"


def _etiqueta_dia(dias_extra: int, fecha: date) -> str:
    # El día y la fecha van explícitos para que el modelo no tenga que deducirlos.
    prefijo = {0: "hoy ", 1: "mañana "}.get(dias_extra, "el ")
    return prefijo + dia_y_fecha(fecha)


def calcular_horarios(
    ahora: datetime,
    hora_inicio: int = config.HORA_INICIO,
    hora_fin: int = config.HORA_FIN,
    margen_horas: int = config.MARGEN_HORAS,
    cantidad: int = 2,
) -> str:
    """Primer horario libre de cada día, respetando el margen mínimo.

    Devuelve hasta `cantidad` opciones en días distintos, p. ej.
    "hoy viernes 30 de octubre a las 5 PM o mañana sábado 31 de octubre a las 2 PM".
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
