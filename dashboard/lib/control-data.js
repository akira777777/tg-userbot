import "server-only";

function setting(name) { return process.env[name]?.trim() ?? ""; }

async function readTable(table, query) {
  const url = setting("SUPABASE_URL");
  const key = setting("SUPABASE_SECRET_KEY");
  if (!url || !key) throw new Error("Cloud mirror is not configured");
  const response = await fetch(`${url}/rest/v1/${table}?${query}`, { headers: { apikey: key, Authorization: `Bearer ${key}` }, cache: "no-store" });
  if (response.status === 404) return { missing: true, rows: [] };
  if (!response.ok) throw new Error("Cloud mirror is unavailable");
  return { missing: false, rows: await response.json() };
}

export async function loadControlSnapshot() {
  const [accounts, campaigns, stats] = await Promise.all([
    readTable("tb_accounts", "select=name,enabled,daily_budget,groups_covered,writable_groups,dm_today,flood_until&order=name"),
    readTable("tb_campaigns", "select=id,kind,account,created_at,ok,fail&order=id.desc&limit=20"),
    readTable("tb_stats", "select=key,value"),
  ]);
  return { schemaMissing: [accounts, campaigns, stats].some((result) => result.missing), accounts: accounts.rows, groups: [], campaigns: campaigns.rows, stats: stats.rows, fetchedAt: new Date().toISOString() };
}
