"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { MODE_LABELS, READ_KINDS, STATUS_LABELS, commandLabel } from "../lib/command-kinds";
import { resultLine, resultOutput } from "../lib/command-results";
import { controlFetch } from "../lib/control-client";
import { friendlyError, formatDateTime, formatRelative } from "../lib/format";
import { hapticImpact } from "../lib/telegram";

const FILTERS = [
  { id: "all", label: "Все" },
  { id: "active", label: "В работе" },
  { id: "done", label: "Завершённые" },
];

const ACTIVE = new Set(["PENDING", "QUEUED", "CLAIMED", "RUNNING"]);

function statusTone(status) {
  if (status === "SUCCEEDED") return "ok";
  if (status === "FAILED" || status === "EXPIRED") return "err";
  if (status === "RUNNING" || status === "CLAIMED") return "warn";
  if (status === "CANCELLED") return "dim";
  return "info";
}

export function humanizeErrorCode(code) {
  const normalized = String(code || "").trim().toUpperCase();
  const known = {
    TIMEOUT: "Воркер не успел выполнить команду.",
    FLOOD_WAIT: "Telegram ограничил частоту — повторите позже.",
    CANCELLED: "Отменено вами.",
    DB_LOCKED: "База данных занята другим процессом — повторите позже.",
    SESSION_LOCKED: "Сессия аккаунта занята — дождитесь окончания текущей задачи.",
    UNAUTHORIZED: "Аккаунт не авторизован в Telegram.",
    AUTH_CONFIG: "Не настроены API_ID / API_HASH на сервере воркера.",
    BUSY: "Слот аккаунта уже занят другой командой.",
    POLICY: "Команда отклонена политикой безопасности.",
    EXPIRED: "Срок аренды LIVE-команды истёк — повторный запуск отключён.",
    FAILED: "Команда завершилась с ошибкой.",
  };
  return known[normalized] || `Ошибка: ${code}`;
}

function errorText(command) {
  if (command.error_code) return humanizeErrorCode(command.error_code);
  const summary = command.result_summary;
  if (summary && typeof summary.error === "string") return summary.error;
  return "";
}

function duration(command) {
  if (!command.claimed_at) return "";
  const start = new Date(command.claimed_at).getTime();
  const end = command.finished_at ? new Date(command.finished_at).getTime() : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "";
  const seconds = Math.round((end - start) / 1000);
  if (seconds < 60) return `${seconds} сек`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} мин ${seconds % 60} сек`;
}

/** Общий поллинг ленты команд (один таймер на всю страницу, без дублирования). */
export function useCommandsFeed(refreshKey, { enabled = true } = {}) {
  const [commands, setCommands] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const { response, body } = await controlFetch("/api/control/commands?limit=20");
    if (!response.ok || !body.ok) throw new Error(body.error || "Не удалось загрузить команды.");
    setCommands(body.commands || []);
    setError("");
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    setLoading(true);
    load()
      .catch((reason) => { if (!cancelled) setError(friendlyError(reason)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      load().catch((reason) => { if (!cancelled) setError(friendlyError(reason)); });
    }, 12_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [enabled, load, refreshKey]);

  // Активные команды опрашиваем чаще: локальный воркер выполняет их за секунды.
  useEffect(() => {
    const active = commands.some((cmd) => ACTIVE.has(cmd.status));
    if (!active) return undefined;
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      load().catch((reason) => setError(friendlyError(reason)));
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [commands, load]);

  return { commands, loading, error, setError, load };
}

/** Панель «выполняется сейчас»: видна, даже если форма далеко вверху. */
export function StickyActionBar({ commands = [] }) {
  const activeCommands = useMemo(() => commands.filter((cmd) => ACTIVE.has(cmd.status)), [commands]);
  if (!activeCommands.length) return null;
  const first = activeCommands[0];
  const fullText = (activeCommands.length === 1 ? "Выполняется" : `Выполняются: ${activeCommands.length}`) +
    ` · ${commandLabel(first.kind)}` + (activeCommands.length > 1 ? " и ещё" : "");
  return (
    <div className="sticky-bar" role="status" aria-live="polite">
      <span className="sticky-bar-dot" aria-hidden="true" />
      <span className="sticky-bar-text" title={fullText}>{fullText}</span>
      <a className="sticky-bar-link" href="#queue">К очереди ↓</a>
    </div>
  );
}

export default function CommandTimeline({
  commands = [],
  loading = false,
  error = "",
  setError = () => {},
  load = async () => {},
  localMode = false,
}) {
  const [busyId, setBusyId] = useState("");
  const [filter, setFilter] = useState("all");
  const [confirmCancelId, setConfirmCancelId] = useState("");

  async function cancel(commandId) {
    setBusyId(commandId);
    setConfirmCancelId("");
    try {
      const { response, body } = await controlFetch(`/api/control/commands/${encodeURIComponent(commandId)}`, {
        method: "POST",
      });
      if (!response.ok || !body.ok) throw new Error(body.error || "Не удалось отменить");
      hapticImpact("medium");
      await load();
    } catch (reason) {
      setError(friendlyError(reason));
    } finally {
      setBusyId("");
    }
  }

  const counts = useMemo(() => ({
    active: commands.filter((cmd) => ACTIVE.has(cmd.status)).length,
  }), [commands]);

  const visible = useMemo(() => {
    if (filter === "active") return commands.filter((cmd) => ACTIVE.has(cmd.status));
    if (filter === "done") return commands.filter((cmd) => !ACTIVE.has(cmd.status));
    return commands;
  }, [commands, filter]);

  return (
    <section className="panel" id="queue" aria-labelledby="queue-title">
      <div className="panel-head">
        <h2 id="queue-title">Очередь команд</h2>
        {counts.active > 0 && <span className="chip info">{counts.active} в работе</span>}
      </div>

      <div className="tabs" role="tablist" aria-label="Фильтр очереди">
        {FILTERS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={filter === item.id}
            className={`tab${filter === item.id ? " active" : ""}`}
            onClick={() => setFilter(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="notice error" role="alert">
          <p>{error}</p>
          <button
            type="button"
            className="ghost"
            onClick={() => {
              setError("");
              load().catch((reason) => setError(friendlyError(reason)));
            }}
          >
            Повторить
          </button>
        </div>
      )}
      {loading && !commands.length && (
        <div className="skeleton-group" aria-hidden="true">
          <div className="skeleton skeleton-row" />
          <div className="skeleton skeleton-row" />
        </div>
      )}
      {!loading && !commands.length && (
        <p className="empty">
          Пока пусто. Выберите сценарий выше: проверка без отправки ничего не отправит.
        </p>
      )}
      {!loading && commands.length > 0 && !visible.length && (
        <p className="empty">{filter === "active" ? "Сейчас ничего не выполняется." : "Завершённых команд пока нет."}</p>
      )}

      <ol className="timeline">
        {visible.map((cmd, idx) => {
          const isActive = ACTIVE.has(cmd.status);
          const canCancel = cmd.status === "PENDING" || cmd.status === "QUEUED" || (localMode && cmd.status === "RUNNING");
          const line = resultLine(cmd.result_summary);
          const output = resultOutput(cmd.result_summary);
          const autoOpenOutput = idx === 0 && (READ_KINDS.has(cmd.kind) || cmd.mode === "DRY_RUN");
          return (
            <li className={`event event-${statusTone(cmd.status)}`} key={cmd.id}>
              <div className="event-top">
                <span className="event-kind">{commandLabel(cmd.kind)}</span>
                <span className={`chip ${statusTone(cmd.status)}`}>{STATUS_LABELS[cmd.status] || cmd.status}</span>
              </div>
              <div className="event-meta">
                {MODE_LABELS[cmd.mode] || cmd.mode}
                {` · ${formatDateTime(cmd.created_at)}`}
                {` · ${formatRelative(cmd.created_at)}`}
                {isActive && <span className="pulse-dot" aria-label="выполняется" />}
              </div>
              {(line || errorText(cmd) || duration(cmd)) && (
                <div className="event-meta">
                  {line && <span>{line}</span>}
                  {duration(cmd) && <span>{line ? " · " : ""}{duration(cmd)}</span>}
                </div>
              )}
              {output && (
                <details className="event-output" open={autoOpenOutput}>
                  <summary>Вывод команды</summary>
                  <pre className="event-output-pre">{output}</pre>
                </details>
              )}
              {errorText(cmd) && <p className="event-error">{errorText(cmd)}</p>}
              {canCancel && confirmCancelId !== cmd.id && (
                <button className="ghost" type="button" disabled={busyId === cmd.id}
                  onClick={() => { setConfirmCancelId(cmd.id); hapticImpact("light"); }}>
                  {busyId === cmd.id ? "Отмена…" : "Отменить"}
                </button>
              )}
              {confirmCancelId === cmd.id && (
                <div className="inline-confirm">
                  <span className="hint">Отменить до выполнения?</span>
                  <button className="ghost danger" type="button" onClick={() => cancel(cmd.id)} disabled={busyId === cmd.id}>
                    Да, отменить
                  </button>
                  <button className="link" type="button" onClick={() => setConfirmCancelId("")}>Не надо</button>
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
