"use client";

import { useCallback, useEffect, useState } from "react";
import { MODE_LABELS, STATUS_LABELS, commandLabel } from "../lib/command-kinds";
import { formatDateTime, formatRelative } from "../lib/format";

function statusClass(status) {
  if (status === "SUCCEEDED") return "ok";
  if (status === "FAILED") return "err";
  if (status === "RUNNING") return "warn";
  if (status === "QUEUED") return "info";
  return "dim";
}

export default function CommandTimeline({ refreshKey = 0 }) {
  const [commands, setCommands] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");

  const load = useCallback(async () => {
    const response = await fetch("/api/control/commands?limit=20", { cache: "no-store" });
    const body = await response.json();
    if (!response.ok || !body.ok) throw new Error(body.error || "Не удалось загрузить команды.");
    setCommands(body.commands || []);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    load()
      .catch((reason) => { if (!cancelled) setError(reason.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    const timer = window.setInterval(() => {
      load().catch((reason) => setError(reason.message));
    }, 12_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [load, refreshKey]);

  async function cancel(commandId) {
    setBusyId(commandId);
    try {
      const response = await fetch(`/api/control/commands/${encodeURIComponent(commandId)}`, { method: "POST" });
      const body = await response.json();
      if (!response.ok || !body.ok) throw new Error(body.error || "Не удалось отменить");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось отменить");
    } finally {
      setBusyId("");
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Очередь команд</h2>
      </div>
      {error && <p className="notice error" role="alert">{error}</p>}
      {loading && !commands.length && <div className="skeleton" aria-hidden="true" />}
      {!loading && !commands.length && <p className="empty">Команд пока нет — поставь первую из формы выше.</p>}
      <div className="timeline">
        {commands.slice(0, 20).map((cmd) => (
          <article className="event" key={cmd.id}>
            <div className="event-top">
              <span className="event-kind">{commandLabel(cmd.kind)}</span>
              <span className={`chip ${statusClass(cmd.status)}`}>{STATUS_LABELS[cmd.status] || cmd.status}</span>
            </div>
            <div className="event-meta">
              {MODE_LABELS[cmd.mode] || cmd.mode} · {formatDateTime(cmd.created_at)} · {formatRelative(cmd.created_at)}
            </div>
            {cmd.error_code && <div className="event-meta" style={{ color: "var(--err)" }}>{cmd.error_code}</div>}
            {cmd.status === "QUEUED" && (
              <button className="ghost" type="button" disabled={busyId === cmd.id} onClick={() => cancel(cmd.id)} style={{ marginTop: 8 }}>
                {busyId === cmd.id ? "Отмена…" : "Отменить"}
              </button>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
