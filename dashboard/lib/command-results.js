/**
 * Сводка результата. Воркер кладёт сюда {ok: boolean, account, output},
 * а кампании — числа (ok/fail/sent). Boolean в `ok` — признак выполнения,
 * его в текст «успешно: true» превращать нельзя.
 */
export function resultLine(summary) {
  if (!summary || typeof summary !== "object") return "";
  const parts = [];
  const numeric = (value) => typeof value === "number" && Number.isFinite(value);
  if (numeric(summary.sent)) parts.push(`отправлено: ${summary.sent}`);
  if (numeric(summary.ok)) parts.push(`успешно: ${summary.ok}`);
  if (numeric(summary.fail)) parts.push(`ошибок: ${summary.fail}`);
  if (numeric(summary.total)) parts.push(`всего: ${summary.total}`);
  if (!parts.length && summary.ok === true) parts.push("команда выполнена");
  return parts.join(" · ");
}

/** Текстовый вывод выполненной команды (STATUS, GROUPS, CAMPAIGNS, DRY_RUN и др.). */
export function resultOutput(summary) {
  if (!summary || typeof summary !== "object") return "";
  if (typeof summary.output !== "string") return "";
  return summary.output.trim();
}
