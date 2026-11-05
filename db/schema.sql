-- Esquema de Supabase (Postgres) para Sofía.
-- Ejecutar en el SQL Editor de Supabase. Es idempotente.

create table if not exists public.mensajes (
    id            bigint generated always as identity primary key,
    telefono      text        not null,
    rol           text        not null check (rol in ('user', 'assistant')),
    contenido     text        not null,
    wa_message_id text,
    creado_en     timestamptz not null default now()
);

-- Idempotencia: Meta puede reenviar el mismo mensaje. Los NULL (respuestas) no chocan.
create unique index if not exists mensajes_wa_message_id_key
    on public.mensajes (wa_message_id);

-- Historial por conversación (consulta más frecuente).
create index if not exists mensajes_telefono_creado_en_idx
    on public.mensajes (telefono, creado_en desc);

-- El backend usa la service key; se bloquea el acceso público.
alter table public.mensajes enable row level security;

-- Estado por conversación. venta_pausada=true: Sofía deja de vender (crisis detectada)
-- y solo responde con contención hasta que Eduardo la reanude.
create table if not exists public.conversaciones (
    telefono       text        primary key,
    venta_pausada  boolean     not null default false,
    motivo_pausa   text,
    pausada_en     timestamptz,
    actualizado_en timestamptz not null default now()
);

alter table public.conversaciones enable row level security;

-- Día 4: Eduardo toma la conversación. humano_activo=true: Sofía no responde y reenvía
-- cada mensaje a Telegram hasta que Eduardo use /reanudar.
alter table public.conversaciones add column if not exists humano_activo boolean not null default false;
alter table public.conversaciones add column if not exists motivo_humano text;
alter table public.conversaciones add column if not exists humano_desde  timestamptz;

-- Día 5: recordatorios de sesión ya enviados. Se registran antes de enviar para que un
-- reinicio o dos revisiones seguidas no repitan el WhatsApp.
create table if not exists public.recordatorios (
    evento_uid text        primary key,
    telefono   text        not null,
    inicio     timestamptz not null,
    enviado_en timestamptz not null default now()
);

alter table public.recordatorios enable row level security;

-- Día 9: retención de 90 días. Borra todo lo de las personas que llevan más de `dias` sin escribir
-- (historial, estado y recordatorios). Devuelve cuántas personas y mensajes se borraron.
create or replace function public.purgar_inactivos(dias integer default 90)
returns table (personas integer, mensajes integer)
language plpgsql
security definer
set search_path = public
as $$
declare
    corte timestamptz := now() - make_interval(days => dias);
begin
    drop table if exists inactivos;
    create temporary table inactivos on commit drop as
        select telefono from public.mensajes group by telefono having max(creado_en) < corte;

    delete from public.recordatorios where telefono in (select telefono from inactivos);
    delete from public.conversaciones
        where telefono in (select telefono from inactivos)
           or (actualizado_en < corte and telefono not in (select telefono from public.mensajes));
    with borrados as (
        delete from public.mensajes where telefono in (select telefono from inactivos) returning 1
    )
    select (select count(*) from inactivos)::integer, count(*)::integer into personas, mensajes from borrados;
    return next;
end;
$$;

-- Solo el backend (service_role) y pg_cron pueden ejecutarla
revoke all on function public.purgar_inactivos(integer) from public, anon, authenticated;

-- Todos los días a las 9:00 UTC (3 AM en México)
create extension if not exists pg_cron;
select cron.schedule('sofia-retencion-90-dias', '0 9 * * *', $$select public.purgar_inactivos(90)$$);

-- Día 9: métricas de operación, una fila por respuesta. Sin teléfono ni contenido a propósito: por eso
-- no entran en la retención de 90 días.
create table if not exists public.metricas (
    id              bigint generated always as identity primary key,
    creado_en       timestamptz not null default now(),
    fase            text        not null,
    modelo          text        not null,
    latencia_ms     integer     not null,
    tokens_entrada  integer     not null,
    tokens_salida   integer     not null,
    llamadas_claude integer     not null,
    costo_usd       numeric(12, 8)
);

create index if not exists metricas_creado_en_idx on public.metricas (creado_en desc);

alter table public.metricas enable row level security;

-- Día 9: cuándo recibió la persona el aviso de privacidad (una sola vez). Si la retención de 90 días borra su
-- conversación, lo vuelve a recibir si regresa.
alter table public.conversaciones add column if not exists aviso_privacidad_en timestamptz;
