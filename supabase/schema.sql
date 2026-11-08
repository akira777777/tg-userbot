-- Схема облачного зеркала tg-userbot (только агрегаты, без личных ников).
-- Выполнить ОДИН РАЗ в Supabase Dashboard → SQL Editor.

create table if not exists tb_accounts (
  name text primary key,
  user_id bigint, api_id bigint,
  enabled boolean, daily_budget int,
  flood_until text, note text,
  groups_covered int default 0,
  writable_groups int default 0,
  dm_today int default 0,
  synced_at timestamptz default now()
);

create table if not exists tb_groups (
  id bigint primary key,
  title text, username text, gtype text,
  in_broadcast boolean, active boolean,
  can_write_agg text,                 -- 'yes' / 'no' / 'mixed' по аккаунтам
  members_collected int default 0,
  synced_at timestamptz default now()
);

create table if not exists tb_campaigns (
  id int primary key,
  kind text, account text, text text,
  created_at text, ok int default 0, fail int default 0,
  synced_at timestamptz default now()
);

create table if not exists tb_sent (
  campaign int, target text, account text,
  status text, ts text,
  primary key (campaign, target, account)
);

create table if not exists tb_blacklist (
  user_id bigint primary key,
  username text, reason text, ts text,
  synced_at timestamptz default now()
);

create table if not exists tb_stats (
  key text primary key, value text,
  synced_at timestamptz default now()
);

-- --------------------------------------------------------
-- Очередь команд Mini App (контрольная плоскость)
-- --------------------------------------------------------

create table if not exists control_commands (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null,
  owner_telegram_id bigint not null,
  kind text not null check (kind in ('STATUS','GROUPS','CAMPAIGNS','MEDIA_LIST','BLACKLIST_ADD','BROADCAST','DMSEND','CONDUCT','STOP')),
  mode text not null check (mode in ('READ','DRY_RUN','LIVE')),
  account text,
  payload jsonb not null,
  request_digest text not null,
  status text not null default 'PENDING' check (status in ('PENDING','CLAIMED','RUNNING','SUCCEEDED','FAILED','CANCELLED','EXPIRED')),
  created_at timestamptz not null default now(),
  claimed_by text,
  claimed_at timestamptz,
  lease_expires_at timestamptz,
  finished_at timestamptz,
  result_summary jsonb,
  error_code text,
  version integer not null default 0
);

create unique index if not exists idx_control_commands_idempotent
  on control_commands (owner_telegram_id, idempotency_key);

create index if not exists idx_control_commands_queue
  on control_commands (status, created_at)
  where status in ('PENDING','CLAIMED','RUNNING');

create table if not exists control_command_events (
  command_id uuid not null references control_commands(id) on delete cascade,
  sequence integer not null,
  occurred_at timestamptz not null default now(),
  level text not null check (level in ('info','warn','error')),
  event_type text not null,
  safe_message text not null,
  primary key (command_id, sequence)
);

create table if not exists control_confirmations (
  nonce uuid primary key default gen_random_uuid(),
  owner_telegram_id bigint not null,
  request_digest text not null,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);

-- RPC: атоматьное взятие команд в аренду воркером
do $$
begin
  create or replace function claim_control_commands(
    p_worker_id text,
    p_batch_size integer default 5,
    p_lease_seconds integer default 300
  )
  returns table (
    id uuid,
    idempotency_key uuid,
    owner_telegram_id bigint,
    kind text,
    mode text,
    account text,
    payload jsonb,
    status text,
    created_at timestamptz,
    claimed_at timestamptz,
    lease_expires_at timestamptz,
    version integer
  )
  language plpgsql as $$
  declare
    v_now timestamptz := now();
    v_lease timestamptz := v_now + (p_lease_seconds || ' seconds')::interval;
  begin
    -- Возвращаем команды, которые можно взять: PENDING или истёкший lease у RUNNING/CLAIMED
    return query
    update control_commands
    set status = 'CLAIMED',
        claimed_by = p_worker_id,
        claimed_at = v_now,
        lease_expires_at = v_lease,
        version = version + 1
    where id in (
      select id from control_commands
      where (status = 'PENDING'
             or (status in ('CLAIMED','RUNNING') and lease_expires_at < v_now))
        and owner_telegram_id = owner_telegram_id  -- ограничение по владельцу
      order by created_at asc
      limit p_batch_size
      for update skip locked
    )
    returning id, idempotency_key, owner_telegram_id, kind, mode, account,
              payload, status, created_at, claimed_at, lease_expires_at, version;
  end;
  $$;
end $$;

-- RLS: только service_role может читать/писать контрольные таблицы
alter table control_commands enable row level security;
alter table control_commands force row level security;
alter table control_command_events enable row level security;
alter table control_command_events force row level security;
alter table control_confirmations enable row level security;
alter table control_confirmations force row level security;

drop policy if exists service_all on control_commands;
create policy service_all on control_commands for all to service_role using (true) with check (true);

drop policy if exists service_all on control_command_events;
create policy service_all on control_command_events for all to service_role using (true) with check (true);

drop policy if exists service_all on control_confirmations;
create policy service_all on control_confirmations for all to service_role using (true) with check (true);

-- Anon и authenticated роли не имеют доступа к контрольным таблицам (по умолчанию запрещено RLS)

-- Realtime для событий команд (опционально, для UI polling)
alter table control_commands replica identity full;
alter table control_command_events replica identity full;

do $$
begin
  begin
    alter publication supabase_realtime add table control_commands;
  exception when duplicate_object then null;
  end;
  begin
    alter publication supabase_realtime add table control_command_events;
  exception when duplicate_object then null;
  end;
end $$;

-- --------------------------------------------------------
-- RLS для существующих агрегированных таблиц: убрать anon_read с чувствительных
-- --------------------------------------------------------
-- tb_blacklist больше не читается anon
do $$
declare t text;
begin
  foreach t in array array['tb_blacklist'] loop
    execute format('drop policy if exists anon_read on %I', t);
  end loop;
end $$;
