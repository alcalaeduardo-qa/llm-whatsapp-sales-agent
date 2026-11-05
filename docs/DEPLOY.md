# Deploy en Railway

Railway construye la imagen con el `Dockerfile` y usa `railway.toml`. Sofía corre como **una sola réplica**: los recordatorios viven en un bucle dentro del proceso.

## Antes de empezar

1. Aplica en Supabase la migración de `db/schema.sql` (tabla `metricas` y columna `aviso_privacidad_en`). **Va antes que el código:** sin la columna falla la lectura del estado y Sofía respondería a conversaciones en modo humano.
2. Guarda una copia de `prompts/sofia.md` fuera del repo.

## 1. Crear el servicio

1. En railway.com: **New Project → Deploy from GitHub repo →** `llm-whatsapp-sales-agent`, rama `main`.
2. En **Variables → Raw Editor** pega el contenido de tu `.env`. Revisa que `LOG_CONTENIDO=false`.
3. Agrega `SOFIA_PROMPT` con el contenido completo de `prompts/sofia.md`. Sin ella, Sofía usa la plantilla pública.
4. En **Settings → Networking → Generate Domain** obtén la URL `https://<algo>.up.railway.app`.

## 2. Probar antes de cambiar los webhooks

```bash
curl https://<algo>.up.railway.app/health
```

Debe responder `{"status":"ok"}`. Hasta aquí, los mensajes siguen llegando a tu computadora.

## 3. Cambiar los webhooks

Apaga primero el servidor local y ngrok, para que no haya dos procesos mandando recordatorios.

1. **Meta:** App `sofia-v2` → WhatsApp → Configuration → Callback URL `https://<dominio>/webhook`, con el mismo `VERIFY_TOKEN`.
2. **Telegram**, desde tu computadora:

```bash
.venv/Scripts/python.exe -m app.telegram https://<dominio>
```

## 4. Probar en vivo

- Mensaje nuevo por WhatsApp: Sofía responde y el aviso de privacidad llega solo una vez.
- Nota de voz: se transcribe.
- `/ayuda` en Telegram: responde.
- En Supabase aparece una fila en `metricas` por cada respuesta.
- En **Deployments → Logs** no aparece el texto de los mensajes.

## 5. Subdominio de sanandoalmaa.com

`sanandoalmaa.com` no tenía registros DNS el 4 de noviembre de 2026, así que primero hay que activar el dominio.

1. En Railway: **Settings → Networking → Custom Domain**, por ejemplo `sofia.sanandoalmaa.com`.
2. En tu proveedor de dominio crea el registro `CNAME` que indique Railway.
3. Cuando el certificado esté listo, repite el paso 3 con el dominio definitivo.

## Volver a local

Enciende uvicorn y ngrok y regresa los dos webhooks a `https://spore-departure-chloride.ngrok-free.dev`. Detén el servicio en Railway.

## Consultas de métricas

En el SQL Editor de Supabase:

```sql
select date_trunc('day', creado_en at time zone 'America/Mexico_City') as dia,
       count(*) as respuestas,
       round(avg(latencia_ms)) as latencia_promedio_ms,
       percentile_cont(0.95) within group (order by latencia_ms) as latencia_p95_ms,
       sum(costo_usd) as costo_usd
from metricas
group by 1
order by 1 desc;

select fase, count(*) as respuestas, avg(costo_usd) as costo_promedio_usd
from metricas
group by 1
order by 2 desc;
```
