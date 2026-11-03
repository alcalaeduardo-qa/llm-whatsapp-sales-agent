import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

ZONA_MX = ZoneInfo("America/Mexico_City")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
APP_SECRET = os.getenv("APP_SECRET", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v26.0")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
# claude-haiku-5-5 razona antes de responder y ese razonamiento cuenta contra max_tokens;
# con un límite bajo se corta antes de escribir el mensaje. El largo visible lo limita el validador.
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "16000"))
# Profundidad del razonamiento (low, medium, high). "low" es lo recomendado para chat.
SOFIA_EFFORT = os.getenv("SOFIA_EFFORT", "low")
HISTORIAL_MAX = int(os.getenv("HISTORIAL_MAX", "20"))

# Horario de atención (hora México). La última sesión inicia a HORA_FIN - 1.
HORA_INICIO = int(os.getenv("HORA_INICIO", "14"))
HORA_FIN = int(os.getenv("HORA_FIN", "20"))
MARGEN_HORAS = int(os.getenv("MARGEN_HORAS", "2"))
SESION_MINUTOS = int(os.getenv("SESION_MINUTOS", "60"))

# Calendario de Eduardo en iCloud (CalDAV). La contraseña es una "contraseña de app" de appleid.apple.com,
# no la del Apple ID. Sin estos datos Sofía ofrece horarios calculados sin consultar el calendario.
ICLOUD_USUARIO = os.getenv("ICLOUD_USUARIO", "")
ICLOUD_PASSWORD_APP = os.getenv("ICLOUD_PASSWORD_APP", "")
ICLOUD_CALENDARIO = os.getenv("ICLOUD_CALENDARIO", "Casa")

# Recordatorio por WhatsApp a la persona antes de su sesión. Si pasaron más de 24 h desde su último
# mensaje, WhatsApp solo acepta una plantilla aprobada (cuerpo con un parámetro: la hora).
RECORDATORIO_MINUTOS_ANTES = int(os.getenv("RECORDATORIO_MINUTOS_ANTES", "10"))
WHATSAPP_PLANTILLA_RECORDATORIO = os.getenv("WHATSAPP_PLANTILLA_RECORDATORIO", "")
WHATSAPP_PLANTILLA_IDIOMA = os.getenv("WHATSAPP_PLANTILLA_IDIOMA", "es_MX")

# Guardrails
CRISIS_LLM = os.getenv("CRISIS_LLM", "true").lower() == "true"
CLAUDE_MODEL_CLASIFICADOR = os.getenv("CLAUDE_MODEL_CLASIFICADOR", CLAUDE_MODEL)
MAX_CARACTERES = int(os.getenv("MAX_CARACTERES", "900"))
MAX_PARRAFOS = int(os.getenv("MAX_PARRAFOS", "4"))

# Alertas a Eduardo por Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# Secreto que Telegram manda en cada update; se elige libremente (A-Z, a-z, 0-9, _ y -).
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

# En producción no se registra el contenido de los mensajes (datos sensibles).
LOG_CONTENIDO = os.getenv("LOG_CONTENIDO", "false").lower() == "true"

DATOS_PAGO = {
    "banco": os.getenv("BANCO", ""),
    "clabe": os.getenv("CLABE", ""),
    "titular": os.getenv("TITULAR", ""),
    "link_stripe": os.getenv("LINK_STRIPE", ""),
    "link_guia": os.getenv("LINK_GUIA", ""),
}
