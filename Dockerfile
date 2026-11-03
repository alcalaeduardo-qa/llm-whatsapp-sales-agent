# Imagen de producción de Sofía en dos etapas: la primera instala dependencias en un entorno virtual y la
# segunda solo copia ese entorno y el código, sin herramientas de compilación.
# El prompt real NO va en la imagen: se pasa en la variable de entorno SOFIA_PROMPT (ver .dockerignore).

FROM python:3.14-slim AS dependencias
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt

FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000
WORKDIR /app
RUN useradd --create-home --uid 1000 sofia
COPY --from=dependencias /opt/venv /opt/venv
COPY app ./app
COPY prompts/sofia.example.md ./prompts/sofia.example.md
USER sofia

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"

# Railway y otras plataformas asignan el puerto en $PORT
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
