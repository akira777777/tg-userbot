"use client";

import { useCallback, useEffect, useState } from "react";
import CommandForm from "../components/command-form";
import CommandTimeline from "../components/command-timeline";
import SnapshotView from "../components/snapshot-view";
import { formatRelative } from "../lib/format";

const REFRESH_MS = 15_000;

function renderedSnapshot(data) {
  return JSON.stringify({
    accounts: data.accounts ?? [],
    campaigns: data.campaigns ?? [],
    stats: data.stats ?? [],
    schemaMissing: Boolean(data.schemaMissing),
  });
}

export default function Home() {
  const [state, setState] = useState("authorizing");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [fetchedAt, setFetchedAt] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    const response = await fetch("/api/control/bootstrap", { cache: "no-store" });
    const body = await response.json();
    if (!response.ok || !body.ok) throw new Error(body.error || "Не удалось загрузить панель.");
    setFetchedAt(new Date().toISOString());
    setData((current) => {
      if (current && renderedSnapshot(current) === renderedSnapshot(body.data)) return current;
      return body.data;
    });
  }, []);

  const refreshAll = useCallback(async () => {
    setRefreshing(true);
    try {
      await load();
      setRefreshKey((value) => value + 1);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setRefreshing(false);
    }
  }, [load]);

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
        window.Telegram.WebApp.ready();
        window.Telegram.WebApp.expand();
        const session = await fetch("/api/control/session", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ initData }),
        });
        if (!session.ok) throw new Error("Telegram не подтвердил доступ к панели.");
        if (cancelled) return;
        await load();
        setState("ready");
        startTimer();
        document.addEventListener("visibilitychange", onVisibility);
      } catch (reason) {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : "Не удалось открыть панель.");
        setState("error");
      }
    }
    start();
    return () => {
      cancelled = true;
      stopTimer();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [load]);

  return (
    <main className="wrap">
      <header className="hero">
        <div>
          <p className="eyebrow">TG USERBOT</p>
          <h1>Панель управления</h1>
          <p className="muted">
            Доступ только для владельца через Telegram.
            {fetchedAt && <> Обновлено {formatRelative(fetchedAt)}.</>}
          </p>
        </div>
        {state === "ready" && (
          <button className="icon-btn" type="button" onClick={refreshAll} disabled={refreshing}>
            {refreshing ? "…" : "Обновить"}
          </button>
        )}
      </header>
      {state === "authorizing" && (
        <>
          <p className="notice">Проверяю вход через Telegram…</p>
          <div className="skeleton" />
        </>
      )}
      {state === "error" && <p className="notice error" role="alert">{error}</p>}
      {state === "ready" && data && (
        <>
          {error && <p className="notice error" role="alert">{error}</p>}
          <SnapshotView data={data} />
          <CommandForm onQueued={() => setRefreshKey((value) => value + 1)} />
          <CommandTimeline refreshKey={refreshKey} />
        </>
      )}
    </main>
  );
}
