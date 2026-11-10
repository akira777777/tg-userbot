export const READ_KINDS = new Set(["STATUS", "GROUPS", "CAMPAIGNS", "MEDIA_LIST"]);
export const DELIVERY_KINDS = new Set(["BROADCAST", "DMSEND", "CONDUCT"]);

export const COMMANDS = [
  { kind: "STATUS", label: "Статус", hint: "Снимок аккаунтов и лимитов без изменений." },
  { kind: "GROUPS", label: "Группы", hint: "Список групп, которые видит воркер." },
  { kind: "CAMPAIGNS", label: "Кампании", hint: "Последние запуски рассылок." },
  { kind: "MEDIA_LIST", label: "Медиа", hint: "Доступные вложения для рассылки." },
  { kind: "BROADCAST", label: "Рассылка в группы", hint: "Текст в выбранные чаты. По умолчанию dry-run." },
  { kind: "DMSEND", label: "Личные сообщения", hint: "Ограниченная DM-отправка. По умолчанию dry-run." },
  { kind: "CONDUCT", label: "Провести кампанию", hint: "Рассылка или личные сообщения по выбранным аккаунтам." },
  { kind: "BLACKLIST_ADD", label: "В чёрный список", hint: "Исключить ID или ник из рассылок." },
  { kind: "STOP", label: "Остановить", hint: "Остановить отправку выбранного аккаунта или всех." },
];

export const STATUS_LABELS = {
  PENDING: "В очереди",
  QUEUED: "В очереди",
  CLAIMED: "Выполняется",
  RUNNING: "Выполняется",
  SUCCEEDED: "Готово",
  FAILED: "Ошибка",
  CANCELLED: "Отменена",
  EXPIRED: "Истекла",
};

export const MODE_LABELS = {
  READ: "чтение",
  DRY_RUN: "dry-run",
  LIVE: "live",
};

function groupIdValue(value) {
  if (value === "" || value == null) return null;
  const id = Number(value);
  if (!Number.isInteger(id) || id === 0) return null;
  return id;
}

function delayPair(delayMin, delayMax) {
  const min = Number(delayMin);
  const max = Number(delayMax);
  return {
    delay_min: Number.isFinite(min) && min > 0 ? min : 20,
    delay_max: Number.isFinite(max) && max > 0 ? max : 45,
  };
}

export function payloadFor(kind, {
  text = "",
  delayMin = 20,
  delayMax = 45,
  limit = 20,
  account = "all",
  conductKind = "broadcast",
  groupId = "",
} = {}) {
  if (READ_KINDS.has(kind)) return {};
  if (kind === "BLACKLIST_ADD") return { value: text.trim(), reason: "mini-app" };
  if (kind === "STOP") return { account: account || "all" };
  const trimmed = text.trim();
  const target = groupIdValue(groupId);
  if (kind === "CONDUCT") {
    const mode = conductKind === "dm" ? "dm" : "broadcast";
    const payload = { text: trimmed, kind: mode };
    if (target && mode === "dm") payload.group_id = target;
    if (target && mode === "broadcast") payload.group_ids = [target];
    if (mode === "dm") {
      const parsed = Number(limit);
      payload.limit = Number.isFinite(parsed) ? Math.max(1, Math.min(200, parsed)) : 20;
    }
    return payload;
  }
  const payload = { text: trimmed, ...delayPair(delayMin, delayMax) };
  if (kind === "DMSEND") {
    const parsed = Number(limit);
    payload.limit = Number.isFinite(parsed) ? Math.max(1, Math.min(50, parsed)) : 20;
    if (target) payload.group_id = target;
  }
  if (kind === "BROADCAST" && target) payload.group_ids = [target];
  return payload;
}

export function commandLabel(kind) {
  return COMMANDS.find((item) => item.kind === kind)?.label ?? kind;
}
