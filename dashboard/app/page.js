"use client";

import { useCallback, useEffect, useState } from "react";
import CommandForm from "../components/command-form";
import CommandTimeline, { StickyActionBar, useCommandsFeed } from "../components/command-timeline";
import SnapshotView from "../components/snapshot-view";
import { isFloodActive } from "../lib/command-options";
import { controlFetch, establishControlSession } from "../lib/control-client";
import { friendlyError, formatRelative } from "../lib/format";
import { hapticImpact, initTelegramWebApp } from "../lib/telegram";
import { readBrowserInitData } from "../lib/telegram-init-data";
import { workerStatus } from "../lib/worker-status";

const REFRESH_MS = 15_000;
const WORKER_STALE_MS = 15 * 60_000;

function renderedSnapshot(data) {
  return JSON.stringify({
    accounts: data.accounts ?? [],
    groups: data.groups ?? [],
    campaigns: data.campaigns ?? [],
    stats: data.stats ?? [],
    syncedAt: data.syncedAt ?? null,
    worker: data.worker ?? null,
    localMode: Boolean(data.localMode),
    schemaMissing: Boolean(data.schemaMissing),
  });
}

function workerFreshness(syncedAt, now) {
  if (!syncedAt) {
    return { tone: "dim", label: "данных пока нет" };
  }
  const ts = Date.parse(syncedAt);
  if (!Number.isFinite(ts)) {
    return { tone: "dim", label: "время синхронизации неизвестно" };
  }
  const ageMs = Math.max(0, now - ts);
  const rel = formatRelative(syncedAt, now);
  if (ageMs > WORKER_STALE_MS) {
    return { tone: "warn", label: `снимок устарел (${rel})` };
  }
  return { tone: "ok", label: `воркер синхронизирован ${rel}` };
}

export default function Home() {
  const [state, setState] = useState("authorizing");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [fetchedAt, setFetchedAt] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const feed = useCommandsFeed(refreshKey, { enabled: state === "ready" });

  const load = useCallback(async () => {
    const { response, body } = await controlFetch("/api/control/bootstrap");
    if (!response.ok || !body.ok) throw new Error(body.error || "Не удалось загрузить панель.");
    setFetchedAt(new Date().toISOString());
    setData((current) => {
      if (current && renderedSnapshot(current) === renderedSnapshot(body.data)) return current;
      return body.data;
    });
  }, []);

  const refreshAll = useCallback(async () => {
    setRefreshing(true);
    hapticImpact("light");
    try {
      if (state !== "ready" && readBrowserInitData()) {
        await establishControlSession({ waitMs: 0 });
      }
      await load();
      setError("");
      setState("ready");
      setRefreshKey((value) => value + 1);
    } catch (reason) {
      setError(friendlyError(reason));
      if (!data) setState("error");
    } finally {
      setRefreshing(false);
    }
  }, [data, load, state]);

  useEffect(() => {
    let cancelled = false;
    async function start() {
      try {
        initTelegramWebApp();
        window.Telegram?.WebApp?.ready?.();
        window.Telegram?.WebApp?.expand?.();
        if (readBrowserInitData()) {
          await establishControlSession({ waitMs: 0 });
        }
        if (cancelled) return;
        await load();
        if (cancelled) return;
        setError("");
        setState("ready");
      } catch (reason) {
        if (cancelled) return;
        setError(friendlyError(reason instanceof Error ? reason : "Не удалось открыть панель."));
        setState("error");
      }
    }
    start();
    return () => {
      cancelled = true;
    };
  }, [load]);

  useEffect(() => {
    if (state !== "ready") return undefined;
    let timer = 0;
    const stopTimer = () => {
      if (timer) window.clearInterval(timer);
      timer = 0;
    };
    const startTimer = () => {
      stopTimer();
      if (document.hidden) return;
      timer = window.setInterval(() => {
        if (document.hidden) return;
        load().catch((reason) => setError(friendlyError(reason)));
      }, REFRESH_MS);
    };
    const onVisibility = () => {
      if (document.hidden) {
        stopTimer();
        return;
      }
      load().catch((reason) => setError(friendlyError(reason)));
      startTimer();
    };
    startTimer();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stopTimer();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [load, state]);

  useEffect(() => {
    const tick = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(tick);
  }, []);

  const accounts = data?.accounts ?? [];
  const groups = data?.groups ?? [];
  const activeAccounts = accounts.filter((item) => item.enabled !== false && !isFloodActive(item.flood_until, now)).length;
  const freshness = data?.localMode
    ? { tone: "ok", label: "данные из локальной базы" }
    : workerFreshness(data?.syncedAt, now);
  const worker = workerStatus(data?.worker, now);

  return (
    <main className="wrap">
      <header className="hero">
        <div className="hero-text">
          <p className="eyebrow">TG USERBOT</p>
          <h1>Панель управления</h1>
          {state === "ready" && (
            <>
              <p className="muted hero-status">
                {accounts.length
                  ? <>Активны <b>{activeAccounts}</b> из {accounts.length} аккаунтов · {data.localMode ? "в базе" : "в зеркале"} <b>{groups.length}</b> групп</>
                  : "Данных пока нет — дождитесь синхронизации воркера."}
              </p>
              <p className="hero-freshness">
                <span className={`chip ${freshness.tone}`}>{freshness.label}</span>
                <span className={`chip ${worker.tone}`}>{worker.label}</span>
              </p>
            </>
          )}
        </div>
        {state === "ready" && (
          <button
            className={`icon-btn refresh${refreshing ? " spinning" : ""}`}
            type="button"
            onClick={refreshAll}
            disabled={refreshing}
            aria-label="Обновить данные"
            title={fetchedAt ? `Обновлено ${formatRelative(fetchedAt, now)}` : "Обновить"}
          >
            <span className="refresh-icon" aria-hidden="true">↻</span>
            <span className="icon-btn-text">{refreshing ? "Обновляю…" : "Обновить"}</span>
          </button>
        )}
      </header>

      {state === "authorizing" && (
        <>
          <p className="notice">Загружаю панель…</p>
          <div className="skeleton-group" aria-hidden="true">
            <div className="skeleton" />
            <div className="skeleton" />
            <div className="skeleton" />
          </div>
        </>
      )}
      {state === "error" && (
        <div className="notice error" role="alert">
          <p><strong>Не удалось открыть панель</strong></p>
          <p>{error}</p>
          <button type="button" className="ghost" onClick={refreshAll} disabled={refreshing}>
            {refreshing ? "Проверяю…" : "Попробовать снова"}
          </button>
        </div>
      )}
      {state === "ready" && data && (
        <>
          {error && <div className="notice error" role="alert">{error}</div>}

          <StickyActionBar commands={feed.commands} />

          <CommandForm
            accounts={accounts}
            groups={groups}
            onQueued={() => setRefreshKey((value) => value + 1)}
          />

          <CommandTimeline
            commands={feed.commands}
            loading={feed.loading}
            error={feed.error}
            setError={feed.setError}
            load={feed.load}
            localMode={Boolean(data?.localMode)}
          />

          <details className="overview">
            <summary>
              <span>Сводка и данные</span>
              <span className="summary-tail" aria-hidden="true">
                <span className="when-open">скрыть</span>
                <span className="when-closed">показать</span>
              </span>
            </summary>
            <div className="overview-body">
              <SnapshotView data={data} now={now} />
            </div>
          </details>

          <footer className="foot">
            <span>
              Обновлено {fetchedAt ? formatRelative(fetchedAt, now) : "—"} · автообновление раз в {REFRESH_MS / 1000} сек
            </span>
          </footer>
        </>
      )}
    </main>
  );
}
