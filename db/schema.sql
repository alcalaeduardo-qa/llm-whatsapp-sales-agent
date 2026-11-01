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
