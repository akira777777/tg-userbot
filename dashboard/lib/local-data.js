import "server-only";

import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

import { accountAliasMap, displayNameFor } from "./account-aliases";

/**
 * Локальный снапшот для режима «просто веб-страница»: читает userbot.db
 * напрямую (node:sqlite, read-only) и отдаёт ту же форму, что и облачное
 * зеркало. Никакие секреты наружу не уходят — только name/enabled/бюджеты.
 *
 * Требует Node >= 22.5 (встроенный node:sqlite).
 */

function localDbPath() {
  const configured = process.env.LOCAL_DB_PATH?.trim();
  if (configured) return configured;
  // dev/build запускаются из dashboard/, база лежит в корне репо
  return path.resolve(process.cwd(), "..", "userbot.db");
}

function localRootPath() {
  return path.resolve(process.cwd(), "..");
}

/** Есть ли session-файл аккаунта: авторизоваться без него нельзя. */
function sessionFileFor(name) {
  const root = localRootPath();
  const candidates = [path.join(root, `${name}.session`), path.join(root, "sessions", `${name}.session`)];
  return candidates.find((candidate) => fs.existsSync(candidate)) ?? null;
}

function rows(db, sql, params = []) {
  return db.prepare(sql).all(...params);
}

function one(db, sql, params = []) {
  return rows(db, sql, params)[0] ?? null;
}

/** Чистая трансформация строк локальной базы в форму облачного снапшота. */
export function buildLocalSnapshot(data) {
  const now = new Date().toISOString();
  const aliases = accountAliasMap(data.accounts.map((row) => row?.name));
  const dmToday = new Map(data.dmToday.map((row) => [row.account, row.total]));
  const covered = new Map(data.covered.map((row) => [row.account, row.total]));
  const writable = new Map(data.writable.map((row) => [row.account, row.total]));
  const membersByGroup = new Map(data.membersByGroup.map((row) => [row.group_id, row.total]));
  const sessions = new Map(data.sessions ?? []);

  const order = new Map(aliases.aliases.map((alias, index) => [alias.internal_name, index]));
  const accounts = [...data.accounts]
    // Канонический порядок как в зеркале: main первым, затем по имени.
    .sort((left, right) => (order.get(left?.name) ?? 999) - (order.get(right?.name) ?? 999))
    .map((row) => {
      const alias = aliases.byName.get(row?.name);
      const {
        name: _name,
        session: _session,
        user_id: _user_id,
        api_id: _api_id,
        api_hash: _api_hash,
        note: _note,
        ...rest
      } = row ?? {};
      return {
        ...rest,
        key: alias?.key ?? null,
        display_name: alias?.display_name ?? displayNameFor(row?.name, aliases),
        groups_covered: covered.get(row?.name) ?? 0,
        writable_groups: writable.get(row?.name) ?? 0,
        dm_today: dmToday.get(row?.name) ?? 0,
        has_session: sessions.has(row?.name),
        synced_at: now,
      };
    });

  const groups = data.groups.map((row) => ({
    id: row?.id,
    title: row?.title,
    in_broadcast: Boolean(row?.in_broadcast),
    members_collected: membersByGroup.get(row?.id) ?? 0,
    synced_at: now,
  }));

  const campaigns = data.campaigns.map((row) => ({
    id: row?.id,
    kind: row?.kind,
    account_display: displayNameFor(row?.account, aliases),
    created_at: row?.created_at,
    ok: row?.ok ?? 0,
    fail: row?.fail ?? 0,
  }));

  return {
    schemaMissing: false,
    localMode: true,
    accounts,
    account_aliases: aliases.aliases.map(({ key, display_name }) => ({ key, display_name })),
    groups,
    campaigns,
    stats: data.stats.map((row) => ({ key: row.key, value: row.value, synced_at: now })),
    worker: { last_heartbeat_at: null, online: null, stale_seconds: 600 },
    syncedAt: now,
    fetchedAt: now,
  };
}

/** Имена аккаунтов из локальной базы — для карты псевдонимов маршрутов. */
export async function loadLocalAccountNames() {
  const db = new DatabaseSync(localDbPath(), { readOnly: true });
  try {
    return rows(db, "select name from accounts").map((row) => row?.name);
  } finally {
    db.close();
  }
}

export async function loadLocalSnapshot() {
  const db = new DatabaseSync(localDbPath(), { readOnly: true });
  try {
    const accounts = rows(db, "select name, enabled, daily_budget, broadcast_budget, flood_until, cohort, spam_strikes, last_peerflood_at, registered_at, added_at from accounts");
    const dmToday = rows(db, "select account, count(*) as total from sent where status = 'ok' and date(ts) = date('now') group by account");
    const covered = rows(db, "select account, count(*) as total from account_groups group by account");
    const writable = rows(db, "select account, count(*) as total from account_groups where can_write = 1 group by account");
    const groups = rows(db, "select id, title, in_broadcast from groups");
    const membersByGroup = rows(db, "select source_group as group_id, count(*) as total from members where deleted = 0 group by source_group");
    const campaigns = rows(
      db,
      `select c.id, c.kind, c.account, c.created_at,
              sum(case when s.status = 'ok' then 1 else 0 end) as ok,
              sum(case when s.status like 'fail%' then 1 else 0 end) as fail
       from campaigns c left join sent s on s.campaign = c.id
       group by c.id order by c.id desc limit 20`,
    );
    const groupsTotal = one(db, "select count(*) as total from groups")?.total ?? 0;
    const membersTotal = one(db, "select count(*) as total from members where deleted = 0")?.total ?? 0;
    const groupsBroadcast = one(db, "select count(*) as total from groups where in_broadcast = 1")?.total ?? 0;
    const sentOk = one(db, "select count(*) as total from sent where status = 'ok'")?.total ?? 0;
    const sentFail = one(db, "select count(*) as total from sent where status like 'fail%'")?.total ?? 0;
    const mediaN = one(db, "select count(*) as total from media")?.total ?? 0;
    const stats = [
      { key: "groups_total", value: groupsTotal },
      { key: "members_total", value: membersTotal },
      { key: "groups_broadcast", value: groupsBroadcast },
      { key: "sent_ok_total", value: sentOk },
      { key: "sent_fail_total", value: sentFail },
      { key: "media_n", value: mediaN },
    ];
    return buildLocalSnapshot({ accounts, dmToday, covered, writable, groups, membersByGroup, campaigns, stats });
  } finally {
    db.close();
  }
}
