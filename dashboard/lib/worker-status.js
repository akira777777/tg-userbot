/**
 * Чистый хелпер статуса воркера для шапки панели.
 * worker — поле из bootstrap: { last_heartbeat_at, online, stale_seconds }.
 */

export function workerStatus(worker, now = Date.now()) {
  if (!worker || worker.online === null || !worker.last_heartbeat_at) {
    return { tone: "dim", label: "состояние воркера неизвестно" };
  }
  if (worker.online) {
    const ageMs = Math.max(0, now - Date.parse(worker.last_heartbeat_at));
    const seconds = Math.round(ageMs / 1000);
    const rel = seconds < 60 ? "только что" : `${Math.floor(seconds / 60)} мин назад`;
    return { tone: "ok", label: `воркер онлайн · сигнал ${rel}` };
  }
  return { tone: "warn", label: "воркер офлайн — команды будут ждать в очереди" };
}
