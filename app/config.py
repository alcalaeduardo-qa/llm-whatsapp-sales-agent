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
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "800"))
HISTORIAL_MAX = int(os.getenv("HISTORIAL_MAX", "20"))

# Horario de atención (hora México). La última sesión inicia a HORA_FIN - 1.
HORA_INICIO = int(os.getenv("HORA_INICIO", "14"))
HORA_FIN = int(os.getenv("HORA_FIN", "20"))
MARGEN_HORAS = int(os.getenv("MARGEN_HORAS", "2"))

# Guardrails
CRISIS_LLM = os.getenv("CRISIS_LLM", "true").lower() == "true"
CLAUDE_MODEL_CLASIFICADOR = os.getenv("CLAUDE_MODEL_CLASIFICADOR", CLAUDE_MODEL)
MAX_CARACTERES = int(os.getenv("MAX_CARACTERES", "900"))
MAX_PARRAFOS = int(os.getenv("MAX_PARRAFOS", "4"))

# Alertas a Eduardo por Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# En producción no se registra el contenido de los mensajes (datos sensibles).
LOG_CONTENIDO = os.getenv("LOG_CONTENIDO", "false").lower() == "true"

DATOS_PAGO = {
    "banco": os.getenv("BANCO", ""),
    "clabe": os.getenv("CLABE", ""),
    "titular": os.getenv("TITULAR", ""),
    "link_stripe": os.getenv("LINK_STRIPE", ""),
    "link_guia": os.getenv("LINK_GUIA", ""),
}
