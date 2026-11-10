export const READ_KINDS = new Set(["STATUS", "GROUPS", "CAMPAIGNS", "MEDIA_LIST"]);
export const DELIVERY_KINDS = new Set(["BROADCAST", "DMSEND", "CONDUCT"]);

export const COMMANDS = [
  { kind: "STATUS", label: "Статус", hint: "Снимок аккаунтов и лимитов без изменений." },
  { kind: "GROUPS", label: "Группы", hint: "Список групп, которые видит воркер." },
  { kind: "CAMPAIGNS", label: "Кампании", hint: "Последние запуски рассылок." },
  { kind: "MEDIA_LIST", label: "Медиа", hint: "Доступные вложения для рассылки." },
  { kind: "BROADCAST", label: "Рассылка в группы", hint: "Текст в выбранные чаты. По умолчанию dry-run." },
  { kind: "DMSEND", label: "Личные сообщения", hint: "Ограниченная DM-отправка. По умолчанию dry-run." },
  { kind: "CONDUCT", label: "Провести кампанию", hint: "Запуск подготовленной кампании." },
  { kind: "BLACKLIST_ADD", label: "В чёрный список", hint: "Исключить ID или ник из рассылок." },
  { kind: "STOP", label: "Остановить всё", hint: "Снять очередь и остановить активные отправки." },
];

export const STATUS_LABELS = {
  QUEUED: "В очереди",
  RUNNING: "Выполняется",
  SUCCEEDED: "Готово",
  FAILED: "Ошибка",
  CANCELLED: "Отменена",
};

export const MODE_LABELS = {
  READ: "чтение",
  DRY_RUN: "dry-run",
  LIVE: "live",
};

export function payloadFor(kind, { text = "", delayMin = 20, delayMax = 45, limit = 20 } = {}) {
  if (READ_KINDS.has(kind)) return {};
  if (kind === "BLACKLIST_ADD") return { value: text.trim(), reason: "mini-app" };
  if (kind === "STOP") return { account: "all" };
  const min = Number(delayMin);
  const max = Number(delayMax);
  const payload = {
    text: text.trim(),
    delay_min: Number.isFinite(min) && min > 0 ? min : 20,
    delay_max: Number.isFinite(max) && max > 0 ? max : 45,
  };
  if (kind === "DMSEND") {
    const parsed = Number(limit);
    payload.limit = Number.isFinite(parsed) ? Math.max(1, Math.min(50, parsed)) : 20;
  }
  if (kind === "CONDUCT") payload.kind = "broadcast";
  return payload;
}

export function commandLabel(kind) {
  return COMMANDS.find((item) => item.kind === kind)?.label ?? kind;
}
