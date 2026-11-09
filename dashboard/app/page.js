"use client";

import { useCallback, useEffect, useState } from "react";
import CommandForm from "../components/command-form";

const REFRESH_MS = 15_000;

function statValue(stats, key) { return stats.find((item) => item.key === key)?.value ?? "—"; }

function renderedSnapshot(data) {
  return JSON.stringify({
    accounts: data.accounts ?? [],
    campaigns: data.campaigns ?? [],
    stats: data.stats ?? [],
    schemaMissing: Boolean(data.schemaMissing),
  });
}

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
    setData((current) => {
      if (current && renderedSnapshot(current) === renderedSnapshot(body.data)) return current;
      return body.data;
    });
  }, []);

  useEffect(() => {
    let timer = 0;
    let cancelled = false;
    const stopTimer = () => {
      if (timer) window.clearInterval(timer);
      timer = 0;
    };
    const startTimer = () => {
      stopTimer();
      if (document.hidden) return;
      timer = window.setInterval(() => {
        if (document.hidden) return;
        load().catch((reason) => setError(reason.message));
      }, REFRESH_MS);
    };
    const onVisibility = () => {
      if (document.hidden) {
        stopTimer();
        return;
      }
      load().catch((reason) => setError(reason.message));
      startTimer();
    };
    async function start() {
      try {
        const initData = window.Telegram?.WebApp?.initData;
        if (!initData) throw new Error("Открой панель кнопкой из чата с ботом Telegram.");
        window.Telegram.WebApp.ready(); window.Telegram.WebApp.expand();
        const session = await fetch("/api/control/session", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ initData }) });
        if (!session.ok) throw new Error("Telegram не подтвердил доступ к панели.");
        if (cancelled) return;
        await load(); setState("ready");
        startTimer();
        document.addEventListener("visibilitychange", onVisibility);
      } catch (reason) {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : "Не удалось открыть панель."); setState("error");
      }
    }
    start();
    return () => {
      cancelled = true;
      stopTimer();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [load]);

  return <main className="wrap"><header><p className="eyebrow">TG USERBOT</p><h1>Панель управления</h1><p className="muted">Доступ только для владельца через Telegram.</p></header>
    {state === "authorizing" && <p className="notice">Проверяю вход через Telegram…</p>}
    {state === "error" && <p className="notice error" role="alert">{error}</p>}
    {state === "ready" && data && <><Snapshot data={data} /><CommandForm /></>}
  </main>;
}
