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

-- RLS: сервисный ключ (sb_secret_) пишет и читает всё; anon может только читать
-- (для живых дашбордов на любом устройстве).
do $$
declare t text;
begin
  foreach t in array array['tb_accounts','tb_groups','tb_campaigns','tb_sent','tb_blacklist','tb_stats'] loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format('drop policy if exists service_all on %I', t);
    execute format('create policy service_all on %I for all to service_role using (true) with check (true)', t);
    execute format('drop policy if exists anon_read on %I', t);
    execute format('create policy anon_read on %I for select to anon using (true)', t);
  end loop;
end $$;

-- Realtime: чтобы любой клиент мог подписаться на изменения
alter table tb_accounts replica identity full;
alter table tb_groups   replica identity full;
alter table tb_campaigns replica identity full;
alter table tb_sent     replica identity full;
alter table tb_blacklist replica identity full;
alter table tb_stats    replica identity full;
do $$
declare t text;
begin
  foreach t in array array['tb_accounts','tb_groups','tb_campaigns','tb_sent','tb_blacklist','tb_stats'] loop
    begin
      execute format('alter publication supabase_realtime add table %I', t);
    exception when duplicate_object then null;
    end;
  end loop;
end $$;
