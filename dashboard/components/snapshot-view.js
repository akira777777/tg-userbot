import { formatDateTime, formatNumber } from "../lib/format";

function statValue(stats, key) {
  return stats.find((item) => item.key === key)?.value;
}

function StatusChip({ enabled, floodUntil }) {
  if (floodUntil) return <span className="chip warn">flood</span>;
  return enabled ? <span className="chip ok">активен</span> : <span className="chip dim">отключён</span>;
}

function ResultChip({ ok, fail }) {
  if (fail > 0 && ok === 0) return <span className="chip err">{fail} ошибок</span>;
  if (fail > 0) return <span className="chip warn">{ok} ok / {fail} ошибок</span>;
  return <span className="chip ok">{ok} ok</span>;
}

export default function SnapshotView({ data }) {
  const stats = data.stats ?? [];
  const cards = [
    ["Группы", statValue(stats, "groups_total")],
    ["Участники", statValue(stats, "members_total")],
    ["Активные рассылки", statValue(stats, "groups_broadcast")],
    ["Доставлено", statValue(stats, "sent_ok_total")],
    ["Ошибки", statValue(stats, "sent_fail_total")],
    ["Медиа", statValue(stats, "media_n")],
  ];

  return (
    <>
      <section className="summary" aria-label="Сводка">
        {cards.map(([label, value]) => (
          <article className="card" key={label}>
            <strong>{formatNumber(value)}</strong>
            <span>{label}</span>
          </article>
        ))}
      </section>
      {data.schemaMissing && (
        <p className="notice">Облачная схема ещё не подготовлена. Выполни миграцию перед запуском панели.</p>
      )}
      <section className="panel">
        <h2>Аккаунты</h2>
        <table>
          <thead>
            <tr>
              <th>Псевдоним</th>
              <th>Группы</th>
              <th>DM сегодня</th>
              <th>Статус</th>
            </tr>
          </thead>
          <tbody>
            {(data.accounts ?? []).map((account, index) => (
              <tr key={account.name}>
                <td>Аккаунт {index + 1}</td>
                <td>{formatNumber(account.groups_covered)}</td>
                <td>{formatNumber(account.dm_today)} / {account.daily_budget || "—"}</td>
                <td><StatusChip enabled={account.enabled} floodUntil={account.flood_until} /></td>
              </tr>
            ))}
            {!(data.accounts ?? []).length && (
              <tr><td className="empty" colSpan="4">Нет аккаунтов в зеркале</td></tr>
            )}
          </tbody>
        </table>
      </section>
      <section className="panel">
        <h2>Последние кампании</h2>
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
            {(data.campaigns ?? []).map((campaign) => (
              <tr key={campaign.id}>
                <td>{campaign.id}</td>
                <td>{campaign.kind}</td>
                <td><ResultChip ok={campaign.ok} fail={campaign.fail} /></td>
                <td>{formatDateTime(campaign.created_at)}</td>
              </tr>
            ))}
            {!(data.campaigns ?? []).length && (
              <tr><td className="empty" colSpan="4">Кампаний пока нет</td></tr>
            )}
          </tbody>
        </table>
      </section>
    </>
  );
}
