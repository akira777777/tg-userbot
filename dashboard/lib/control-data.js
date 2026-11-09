import "server-only";

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

export async function loadControlSnapshot() {
  const [accounts, campaigns, stats] = await Promise.all([
    readRows((client) => client.from("tb_accounts").select("name,enabled,daily_budget,groups_covered,writable_groups,dm_today,flood_until").order("name")),
    readRows((client) => client.from("tb_campaigns").select("id,kind,account,created_at,ok,fail").order("id", { ascending: false }).limit(20)),
    readRows((client) => client.from("tb_stats").select("key,value")),
  ]);
  return { schemaMissing: [accounts, campaigns, stats].some((result) => result.missing), accounts: accounts.rows, groups: [], campaigns: campaigns.rows, stats: stats.rows, fetchedAt: new Date().toISOString() };
}
