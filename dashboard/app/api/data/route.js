// Серверный прокси к Supabase-зеркалу. Секретный ключ живёт только в env Vercel
// и никогда не уходит в браузер; доступ к данным — по DASH_TOKEN.
export const dynamic = "force-dynamic";

const SB = () => process.env.SUPABASE_URL;
const KEY = () => process.env.SUPABASE_SECRET_KEY;

async function grab(table, query) {
  const url = `${SB()}/rest/v1/${table}?${query}`;
  try {
    const r = await fetch(url, {
      headers: { apikey: KEY(), Authorization: `Bearer ${KEY()}` },
      cache: "no-store",
    });
    let body = null;
    try { body = await r.json(); } catch (e) { /* пустое тело */ }
    return { status: r.status, body };
  } catch (e) {
    return { status: 0, body: { error: String(e) } };
  }
}

export async function GET(req) {
  const fail = (code, msg) =>
    new Response(JSON.stringify({ ok: false, error: msg }),
      { status: code, headers: { "content-type": "application/json" } });

  if (!SB() || !KEY()) return fail(500, "SUPABASE_URL / SUPABASE_SECRET_KEY не заданы в env");
  const want = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (!process.env.DASH_TOKEN || want !== process.env.DASH_TOKEN)
    return fail(401, "неверный токен доступа");

  const [accounts, groups, campaigns, stats, blacklist, sent] = await Promise.all([
    grab("tb_accounts", "select=*&order=name"),
    grab("tb_groups", "select=*&order=members_collected.desc&limit=300"),
    grab("tb_campaigns", "select=*&order=id.desc&limit=20"),
    grab("tb_stats", "select=*"),
    grab("tb_blacklist", "select=*&limit=200"),
    grab("tb_sent", "select=campaign,target,account,status,ts&order=ts.desc&limit=60"),
  ]);
  const all = [accounts, groups, campaigns, stats, blacklist, sent];
  const schemaMissing = all.some((x) => x.status === 404);
  const fetchedAt = new Date().toISOString();
  return new Response(
    JSON.stringify({
      ok: true, fetched_at: fetchedAt, schema_missing: schemaMissing,
      data: {
        accounts: accounts.body, groups: groups.body, campaigns: campaigns.body,
        stats: stats.body, blacklist: blacklist.body, sent: sent.body,
      },
    }),
    { status: 200, headers: { "content-type": "application/json" } }
  );
}
