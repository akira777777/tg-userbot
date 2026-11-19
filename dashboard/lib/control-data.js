import "server-only";

import { accountAliasMap, displayNameFor } from "./account-aliases";
import { isLocalMode } from "./control-auth";
import { loadLocalSnapshot } from "./local-data";
import { supabaseAdmin } from "./supabase-server";

function schemaMissing(error) {
  const code = String(error?.code ?? "");
  const message = String(error?.message ?? "");
  return code === "PGRST205" || error?.status === 404 || /schema cache|does not exist|not found/i.test(message);
}

async function readRows(build) {
  const { data, error } = await build(supabaseAdmin());
  if (error && schemaMissing(error)) return { missing: true, rows: [] };
  if (error) throw new Error("Cloud mirror is unavailable");
  return { missing: false, rows: data ?? [] };
}

function latestTimestamp(groupsOfRows) {
  let latest = null;
  let latestMs = -Infinity;
  for (const rows of groupsOfRows) {
    for (const row of rows) {
      if (!row?.synced_at) continue;
      const ms = Date.parse(row.synced_at);
      if (Number.isFinite(ms) && ms > latestMs) {
        latestMs = ms;
        latest = row.synced_at;
      }
    }
  }
  return latest;
}

/**
 * Карта псевдонимов аккаунтов для маршрутов команд; живёт в control-queue.js
 * (fetch-клиент). Здесь только трансформация снапшота для bootstrap.
 */
function aliasAccountRows(rows, aliases) {
  return rows.map((row) => {
    const alias = aliases.byName.get(row?.name);
    const { name: _name, ...rest } = row ?? {};
    return {
      ...rest,
      key: alias?.key ?? null,
      display_name: alias?.display_name ?? displayNameFor(row?.name, aliases),
    };
  });
}

function emptyLocalSnapshot() {
  return {
    schemaMissing: true,
    accounts: [],
    account_aliases: [],
    groups: [],
    campaigns: [],
    stats: [],
    worker: workerStatus([], Date.now()),
    syncedAt: null,
    fetchedAt: new Date().toISOString(),
  };
}

function workerStaleSeconds() {
  const parsed = Number.parseInt(process.env.WORKER_STALE_SECONDS ?? "", 10);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : 600;
}

function workerStatus(heartbeatRows, now) {
  const latest = heartbeatRows[0] ?? null;
  if (!latest?.last_heartbeat_at) {
    return { last_heartbeat_at: null, online: null, stale_seconds: workerStaleSeconds() };
  }
  const ts = Date.parse(latest.last_heartbeat_at);
  if (!Number.isFinite(ts)) {
    return { last_heartbeat_at: null, online: null, stale_seconds: workerStaleSeconds() };
  }
  const ageMs = Math.max(0, now - ts);
  return {
    last_heartbeat_at: latest.last_heartbeat_at,
    online: ageMs <= workerStaleSeconds() * 1000,
    stale_seconds: workerStaleSeconds(),
  };
}

export async function loadControlSnapshot() {
  // Локальный режим без Supabase: данные берутся напрямую из userbot.db.
  if (isLocalMode() && !process.env.SUPABASE_URL?.trim()) {
    try {
      return await loadLocalSnapshot();
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      if (/unable to open database|no such table|ENOENT|does not exist/i.test(message)) {
        return emptyLocalSnapshot();
      }
      throw error;
    }
  }
  const [accounts, groups, campaigns, stats, heartbeats] = await Promise.all([
    readRows((client) => client.from("tb_accounts").select("name,enabled,daily_budget,groups_covered,writable_groups,dm_today,flood_until,synced_at").order("name")),
    readRows((client) => client.from("tb_groups").select("id,title,in_broadcast,members_collected,synced_at").order("title").limit(100)),
    readRows((client) => client.from("tb_campaigns").select("id,kind,account,created_at,ok,fail").order("id", { ascending: false }).limit(20)),
    readRows((client) => client.from("tb_stats").select("key,value,synced_at")),
    readRows((client) => client.from("tb_worker_heartbeat").select("worker_id,last_heartbeat_at").order("last_heartbeat_at", { ascending: false }).limit(1)),
  ]);
  const aliases = accountAliasMap(accounts.rows.map((row) => row?.name));
  const parts = [accounts, groups, campaigns, stats];
  return {
    schemaMissing: parts.some((result) => result.missing),
    accounts: aliasAccountRows(accounts.rows, aliases),
    account_aliases: aliases.aliases.map(({ key, display_name }) => ({ key, display_name })),
    groups: groups.rows,
    campaigns: campaigns.rows.map((row) => ({
      ...row,
      account_display: displayNameFor(row?.account, aliases),
      account: undefined,
    })),
    stats: stats.rows,
    worker: workerStatus(heartbeats.rows, Date.now()),
    syncedAt: latestTimestamp([accounts.rows, groups.rows, stats.rows]),
    fetchedAt: new Date().toISOString(),
  };
}
