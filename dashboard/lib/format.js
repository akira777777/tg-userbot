export function formatNumber(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return new Intl.NumberFormat("ru-RU").format(number);
}

export function formatDateTime(value) {
  if (!value) return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 16);
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Prague",
  }).format(date);
}

export function formatRelative(value, now = Date.now()) {
  if (!value) return "";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const delta = Math.round((date.getTime() - now) / 1000);
  const abs = Math.abs(delta);
  const rtf = new Intl.RelativeTimeFormat("ru", { numeric: "auto" });
  if (abs < 60) return rtf.format(Math.trunc(delta), "second");
  if (abs < 3600) return rtf.format(Math.trunc(delta / 60), "minute");
  if (abs < 86400) return rtf.format(Math.trunc(delta / 3600), "hour");
  return rtf.format(Math.trunc(delta / 86400), "day");
}
