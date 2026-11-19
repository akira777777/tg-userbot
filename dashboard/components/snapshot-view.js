import { useMemo, useState } from "react";
import { isFloodActive, sortAccounts } from "../lib/command-options";
import { formatDateTime, formatNumber } from "../lib/format";

function filterGroupsList(groups, query) {
  const needle = query.trim().toLowerCase();
  if (!needle) return groups;
  return groups.filter((group) => String(group.title || group.id).toLowerCase().includes(needle));
}

function statValue(stats, key) {
  return stats.find((item) => item.key === key)?.value;
}

function StatusChip({ enabled, floodUntil, now }) {
  if (isFloodActive(floodUntil, now)) {
    return (
      <span className="chip warn" title={`Ограничение до ${formatDateTime(floodUntil)}`}>
        пауза до {formatDateTime(floodUntil)}
      </span>
    );
  }
  return enabled ? <span className="chip ok">активен</span> : <span className="chip dim">выключен</span>;
}

function ResultChip({ ok, fail }) {
  if (fail > 0 && ok === 0) return <span className="chip err">{fail} ошибок</span>;
  if (fail > 0) return <span className="chip warn">{ok} ok / {fail} ошибок</span>;
  if (!ok && !fail) return <span className="chip dim">нет данных</span>;
  return <span className="chip ok">{ok} ok</span>;
}

function Card({ label, value, hint, tone }) {
  return (
    <article className={`card stat${tone ? ` stat-${tone}` : ""}`}>
      <strong>{formatNumber(value)}</strong>
      <span>{label}</span>
      {hint && <small>{hint}</small>}
    </article>
  );
}

export default function SnapshotView({ data, now = Date.now() }) {
  const stats = data.stats ?? [];
  const accounts = useMemo(() => sortAccounts(data.accounts ?? []), [data.accounts]);
  const groups = data.groups ?? [];
  const campaigns = data.campaigns ?? [];
  const [query, setQuery] = useState("");
  const visibleGroups = useMemo(() => filterGroupsList(groups, query), [groups, query]);
  const failedShare = (() => {
    const ok = Number(statValue(stats, "sent_ok_total")) || 0;
    const fail = Number(statValue(stats, "sent_fail_total")) || 0;
    if (!ok && !fail) return "";
    const share = Math.round((fail / (ok + fail)) * 100);
    return `${share}% от всех попыток`;
  })();

  return (
    <>
      <section className="summary" aria-label="Сводка">
        <Card label="Группы" value={statValue(stats, "groups_total")} hint="в базе" />
        <Card label="Участники" value={statValue(stats, "members_total")} hint="собрано" />
        <Card label="В рассылке" value={statValue(stats, "groups_broadcast")} hint="чатов включено" />
        <Card label="Доставлено" value={statValue(stats, "sent_ok_total")} hint="сообщений" tone="ok" />
        <Card label="Ошибки" value={statValue(stats, "sent_fail_total")} hint={failedShare || "отправок"} tone="err" />
        <Card label="Медиа" value={statValue(stats, "media_n")} hint="вложений" />
      </section>

      {data.schemaMissing && (
        <p className="notice">
          Часть таблиц облака ещё не создана. Запустите миграцию схемы — часть данных может быть пустой.
        </p>
      )}

      <section className="panel collapsible" aria-labelledby="accounts-title">
        <details>
          <summary>
            <span id="accounts-title">Аккаунты</span>
            <span className="chip dim">{accounts.length}</span>
          </summary>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Аккаунт</th>
                  <th>Группы</th>
                  <th>DM сегодня</th>
                  <th>Статус</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((account) => (
                  <tr key={account.key}>
                    <td>{account.display_name || "Аккаунт ?"}</td>
                    <td>{formatNumber(account.groups_covered)}</td>
                    <td>
                      <span className="budget">
                        {formatNumber(account.dm_today)}
                        <i>/</i>
                        {account.daily_budget || "—"}
                      </span>
                    </td>
                    <td><StatusChip enabled={account.enabled} floodUntil={account.flood_until} now={now} /></td>
                  </tr>
                ))}
                {!accounts.length && (
                  <tr><td className="empty" colSpan="4">Нет аккаунтов в облаке</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </details>
      </section>

      <section className="panel collapsible" aria-labelledby="groups-title">
        <details>
          <summary>
            <span id="groups-title">Группы</span>
            <span className="chip dim">{groups.length}</span>
          </summary>
          {groups.length > 8 && (
            <input
              type="search"
              className="table-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Найти группу по названию"
              aria-label="Поиск группы в таблице"
            />
          )}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Название</th>
                  <th>В рассылке</th>
                  <th>Участники</th>
                </tr>
              </thead>
              <tbody>
                {visibleGroups.map((group) => (
                  <tr key={group.id}>
                    <td>{group.title || group.id}</td>
                    <td>{group.in_broadcast ? <span className="chip ok">да</span> : <span className="chip dim">нет</span>}</td>
                    <td>{formatNumber(group.members_collected)}</td>
                  </tr>
                ))}
                {!groups.length && (
                  <tr><td className="empty" colSpan="3">Нет групп в облаке</td></tr>
                )}
                {groups.length > 0 && !visibleGroups.length && (
                  <tr><td className="empty" colSpan="3">По запросу ничего не найдено</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </details>
      </section>

      <section className="panel collapsible" aria-labelledby="campaigns-title">
        <details>
          <summary>
            <span id="campaigns-title">Последние кампании</span>
            <span className="chip dim">{campaigns.length}</span>
          </summary>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Тип</th>
                  <th>Результат</th>
                  <th>Время</th>
                </tr>
              </thead>
              <tbody>
                {campaigns.map((campaign) => (
                  <tr key={campaign.id}>
                    <td className="mono">{campaign.id}</td>
                    <td>{campaign.kind}</td>
                    <td><ResultChip ok={campaign.ok} fail={campaign.fail} /></td>
                    <td>{formatDateTime(campaign.created_at)}</td>
                  </tr>
                ))}
                {!campaigns.length && (
                  <tr><td className="empty" colSpan="4">Кампаний пока нет</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </details>
      </section>
    </>
  );
}