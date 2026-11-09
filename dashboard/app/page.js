"use client";

import { useCallback, useEffect, useState } from "react";
import { waitForTelegramInitData } from "../lib/telegram-init-data";

const REFRESH_MS = 15_000;

function statValue(stats, key) { return stats.find((item) => item.key === key)?.value ?? "—"; }

function Snapshot({ data }) {
  const stats = data.stats ?? [];
  const cards = [["Группы", statValue(stats, "groups_total")], ["Участники", statValue(stats, "members_total")], ["Активные рассылки", statValue(stats, "groups_broadcast")], ["Доставлено", statValue(stats, "sent_ok_total")], ["Ошибки", statValue(stats, "sent_fail_total")], ["Медиа", statValue(stats, "media_n")]];
  return <>
    <section className="summary" aria-label="Сводка">{cards.map(([label, value]) => <article className="card" key={label}><strong>{value}</strong><span>{label}</span></article>)}</section>
    {data.schemaMissing && <p className="notice">Облачная схема ещё не подготовлена. Выполни миграцию перед запуском панели.</p>}
    <section className="panel"><h2>Аккаунты</h2><table><thead><tr><th>Псевдоним</th><th>Группы</th><th>DM сегодня</th><th>Статус</th></tr></thead><tbody>
      {data.accounts.map((account, index) => <tr key={account.name}><td>Аккаунт {index + 1}</td><td>{account.groups_covered}</td><td>{account.dm_today} / {account.daily_budget || "—"}</td><td>{account.enabled ? "Активен" : "Отключён"}</td></tr>)}
      {!data.accounts.length && <tr><td colSpan="4">Нет данных</td></tr>}
    </tbody></table></section>
    <section className="panel"><h2>Последние кампании</h2><table><thead><tr><th>#</th><th>Тип</th><th>Результат</th><th>Время</th></tr></thead><tbody>
      {data.campaigns.map((campaign) => <tr key={campaign.id}><td>{campaign.id}</td><td>{campaign.kind}</td><td>{campaign.ok} ok / {campaign.fail} ошибок</td><td>{String(campaign.created_at ?? "").replace("T", " ").slice(0, 16)}</td></tr>)}
      {!data.campaigns.length && <tr><td colSpan="4">Нет данных</td></tr>}
    </tbody></table></section>
  </>;
}

export default function Home() {
  const [state, setState] = useState("authorizing");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    const response = await fetch("/api/control/bootstrap", { cache: "no-store" });
    const body = await response.json();
    if (!response.ok || !body.ok) throw new Error(body.error || "Не удалось загрузить панель.");
    setData(body.data);
  }, []);

  useEffect(() => {
    let timer;
    async function start() {
      try {
        let initData = "";
        try {
          initData = await waitForTelegramInitData();
        } catch (reason) {
          const host = window.location.hostname;
          if (host !== "127.0.0.1" && host !== "localhost") throw reason;
        }
        const session = await fetch("/api/control/session", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(initData ? { initData } : { local: true }),
        });
        if (!session.ok) throw new Error("Telegram не подтвердил доступ к панели.");
        await load(); setState("ready");
        timer = window.setInterval(() => load().catch((reason) => setError(reason.message)), REFRESH_MS);
      } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось открыть панель."); setState("error"); }
    }
    start();
    return () => window.clearInterval(timer);
  }, [load]);

  return <main className="wrap"><header><p className="eyebrow">TG USERBOT</p><h1>Панель управления</h1><p className="muted">Открой панель кнопкой из чата с ботом.</p></header>
    {state === "authorizing" && <p className="notice">Проверяю вход через Telegram…</p>}
    {state === "error" && <p className="notice error" role="alert">{error}</p>}
    {state === "ready" && data && <><Snapshot data={data} /><section className="panel"><h2>Управление</h2><p>Команды на выполнение будут подключены к единому локальному worker. До этого панель только читает состояние и не может запустить рассылку.</p></section></>}
  </main>;
}
