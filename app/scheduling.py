from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

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


SIN_HORARIOS = "ninguno disponible en los próximos días, Eduardo te propondrá un horario personalmente"


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


def horarios_libres(
    ahora: datetime,
    hora_inicio: int = config.HORA_INICIO,
    hora_fin: int = config.HORA_FIN,
    margen_horas: int = config.MARGEN_HORAS,
    cantidad: int = 2,
    ocupados: Sequence[tuple[datetime, datetime]] = (),
    duracion_minutos: int = config.SESION_MINUTOS,
) -> list[datetime]:
    """Primer horario libre de cada día, respetando el margen mínimo, en hasta `cantidad` días distintos.
    Un horario que se cruza con algo de `ocupados` (calendario de Eduardo) se salta."""
    zona = ahora.tzinfo
    limite = ahora + timedelta(hours=margen_horas)
    inicios = []
    for dias_extra in range(7):
        fecha = (ahora + timedelta(days=dias_extra)).date()
        for hora in range(hora_inicio, hora_fin):
            inicio = datetime.combine(fecha, time(hora), tzinfo=zona)
            fin = inicio + timedelta(minutes=duracion_minutos)
            libre = all(not (inicio < o_fin and o_inicio < fin) for o_inicio, o_fin in ocupados)
            if inicio >= limite and libre:
                inicios.append(inicio)
                break
        if len(inicios) == cantidad:
            break
    return inicios


def _hora_12(momento: datetime) -> str:
    return f"{momento.strftime('%I').lstrip('0')} {momento.strftime('%p')}"


def describir_opcion(inicio: datetime, ahora: datetime) -> str:
    """ "mañana sábado 31 de octubre a las 2 PM" """
    return f"{_etiqueta_dia((inicio.date() - ahora.date()).days, inicio.date())} a las {_hora_12(inicio)}"


def formatear_opciones(inicios: list[datetime], ahora: datetime) -> str:
    return " o ".join(describir_opcion(i, ahora) for i in inicios) if inicios else SIN_HORARIOS


def calcular_horarios(ahora: datetime, **kwargs) -> str:
    """Las opciones ya redactadas, p. ej.
    "hoy viernes 30 de octubre a las 5 PM o mañana sábado 31 de octubre a las 2 PM"."""
    return formatear_opciones(horarios_libres(ahora, **kwargs), ahora)


# Hora equivalente: Claude calculaba mal las zonas horarias ("Houston = 12 PM" cuando era la misma hora).
# zoneinfo resuelve los horarios de verano de cada país en la fecha exacta de la sesión.
ZONAS_EQUIVALENTES = [
    ("Colombia, Perú y Ecuador", "America/Bogota"),
    ("Venezuela", "America/Caracas"),
    ("Chile", "America/Santiago"),
    ("Argentina", "America/Argentina/Buenos_Aires"),
    ("España", "Europe/Madrid"),
    ("Estados Unidos, hora del Este (Nueva York, Miami)", "America/New_York"),
    ("Estados Unidos, hora del Centro (Houston, Chicago, Dallas)", "America/Chicago"),
    ("Estados Unidos, hora de la Montaña (Denver)", "America/Denver"),
    ("Arizona (Phoenix)", "America/Phoenix"),  # no cambia de horario en verano, a diferencia de Denver
    ("Estados Unidos, hora del Pacífico (Los Ángeles)", "America/Los_Angeles"),
    ("Centroamérica (Guatemala, El Salvador, Honduras, Costa Rica)", "America/Guatemala"),
]


def horas_equivalentes(inicios: list[datetime], ahora: datetime) -> str:
    """Tabla para el prompt con la hora local de cada opción en los países más comunes."""
    lineas = []
    for inicio in inicios:
        equivalentes = []
        for nombre, zona in ZONAS_EQUIVALENTES:
            local = inicio.astimezone(ZoneInfo(zona))
            dia = "" if local.date() == inicio.date() else f" del {dia_y_fecha(local.date())}"
            equivalentes.append(f"{nombre} {_hora_12(local)}{dia}")
        lineas.append(f"- {describir_opcion(inicio, ahora)} hora México equivale a: " + "; ".join(equivalentes) + ".")
    return "\n".join(lineas)
